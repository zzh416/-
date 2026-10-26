import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Checkbox, Input, Modal, Skeleton, Space, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { createAIAnswer, fetchAIAnswer, fetchLatestAIAnswer, retryAIAnswer, reviewAIAnswer, type AIAnswerRun } from '../api/aiAnswers'
import { fetchQuestion } from '../api/client'
import { invalidatePersonalizedContent } from '../api/contextInvalidation'
import { SafeMarkdown } from './SafeMarkdown'
import './aiAnswers.css'

interface Props {
  questionId: string
  questionVersion: number
  initialAiAnswer?: AIAnswerRun | null
  effectiveAiAnswerId?: string | null
  confirmedAiAnswerId?: string | null
  hasPrimaryAnswer?: boolean
  disabled?: boolean
}

const pending = (run: AIAnswerRun | null | undefined) => Boolean(run && ['QUEUED', 'PROCESSING'].includes(run.status))

export function AiAnswerPanel({ questionId, questionVersion, initialAiAnswer, effectiveAiAnswerId = null, confirmedAiAnswerId, hasPrimaryAnswer = false, disabled = false }: Props) {
  const cache = useQueryClient()
  const [open, setOpen] = useState(false)
  const [useProfile, setUseProfile] = useState(true)
  const [includeNote, setIncludeNote] = useState(false)
  const [editing, setEditing] = useState(false)
  const [newDraftRequested, setNewDraftRequested] = useState(false)
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [draft, setDraft] = useState('')
  const [actionError, setActionError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const key = ['ai-answer', questionId]
  const query = useQuery({
    queryKey: key,
    queryFn: () => fetchLatestAIAnswer(questionId),
    initialData: initialAiAnswer,
    enabled: (current) => open || pending(current.state.data) || pending(initialAiAnswer),
    refetchInterval: (current) => pending(current.state.data) ? 1500 : false,
  })
  const latestRun = query.data === undefined ? initialAiAnswer : query.data
  const question = useQuery({
    queryKey: ['question', questionId],
    queryFn: () => fetchQuestion(questionId),
    enabled: open && confirmedAiAnswerId === undefined,
  })
  const confirmedRunId = confirmedAiAnswerId !== undefined
    ? confirmedAiAnswerId
    : question.data ? question.data.confirmedAiAnswerId : effectiveAiAnswerId
  const viewingPrevious = Boolean(selectedRunId && selectedRunId !== latestRun?.id)
  const selectedQuery = useQuery({
    queryKey: ['ai-answer-run', selectedRunId],
    queryFn: () => fetchAIAnswer(selectedRunId!),
    enabled: open && viewingPrevious,
  })
  const visibleQuery = viewingPrevious ? selectedQuery : query
  const run = viewingPrevious ? selectedQuery.data : latestRun
  const isPending = pending(run)
  const stale = run ? !run.isCurrent || run.questionVersion !== questionVersion
    || (initialAiAnswer?.id === run.id && !initialAiAnswer.isCurrent) : false
  useEffect(() => {
    if (!initialAiAnswer) return
    cache.setQueryData<AIAnswerRun | null>(['ai-answer', questionId], (previous) => {
      if (!previous || initialAiAnswer.createdAt > previous.createdAt) return initialAiAnswer
      if (initialAiAnswer.id === previous.id && initialAiAnswer.version >= previous.version) return initialAiAnswer
      return previous
    })
  }, [cache, questionId, initialAiAnswer])
  const refreshConsumers = () => invalidatePersonalizedContent(cache)
  const updateRun = (next: AIAnswerRun) => {
    cache.setQueryData(['ai-answer-run', next.id], next)
    cache.setQueryData<AIAnswerRun | null>(key, (previous) =>
      !previous || previous.id === next.id || previous.createdAt < next.createdAt ? next : previous)
    setActionError(null)
    setEditing(false)
    void refreshConsumers()
  }
  const onError = (error: Error) => { setActionError(error.message); void visibleQuery.refetch() }
  const generate = useMutation({
    mutationFn: () => createAIAnswer(questionId, { expectedQuestionVersion: questionVersion, useProfile, includeNote }),
    onSuccess: (next) => { updateRun(next); cache.setQueryData(key, next); setSelectedRunId(null); setSuccess(null); setNewDraftRequested(false) }, onError,
  })
  const retry = useMutation({ mutationFn: () => retryAIAnswer(run!), onSuccess: updateRun, onError })
  const review = useMutation({
    mutationFn: ({ action, content }: { action: 'SAVE_DRAFT' | 'CONFIRM' | 'DISCARD'; content?: string }) => reviewAIAnswer(run!, action, content),
    onSuccess: (next) => {
      updateRun(next)
      setSuccess(next.reviewStatus === 'CONFIRMED'
        ? hasPrimaryAnswer ? 'AI 补充已确认，原答案仍保留。' : 'AI 补充已确认。已有复习清单请重新生成，以使用这份答案。'
        : next.reviewStatus === 'DISCARDED' ? '已停止采用这份 AI 补充。' : '已保存为待核对草稿。')
    }, onError,
  })
  const busy = generate.isPending || retry.isPending || review.isPending
  useEffect(() => {
    if (run?.status === 'SUCCEEDED' || run?.status === 'FAILED') {
      void cache.invalidateQueries({ queryKey: ['questions'] })
      void cache.invalidateQueries({ queryKey: ['question', questionId] })
    }
  }, [cache, questionId, run?.id, run?.status])

  const hasContent = Boolean(run?.content && run.reviewStatus !== 'DISCARDED')
  const canGenerate = !run || stale || run.reviewStatus === 'DISCARDED' || newDraftRequested
    || (run.status === 'FAILED' && !run.canRetry)
  const buttonLabel = pending(latestRun) ? 'AI 补充生成中…' : latestRun?.content || confirmedRunId ? '查看 AI 补充答案' : 'AI 补充答案'
  const selectRun = (id: string | null) => {
    setSelectedRunId(id); setEditing(false); setNewDraftRequested(false); setActionError(null); setSuccess(null)
  }
  return <>
    <div className="ai-answer-entry">
      <Button disabled={disabled} title={disabled ? '请先重新生成当前复习清单' : undefined}
        onClick={() => { setOpen(true); setActionError(null); setSuccess(null) }}>{buttonLabel}</Button>
      {latestRun?.content && latestRun.reviewStatus !== 'DISCARDED' && <Tag color={!latestRun.isCurrent ? 'default' : latestRun.reviewStatus === 'CONFIRMED' ? 'blue' : 'gold'}>
        {!latestRun.isCurrent ? 'AI 补充 · 依据已变化' : latestRun.reviewStatus === 'CONFIRMED' ? 'AI 补充 · 已确认' : 'AI 补充 · 待核对'}
      </Tag>}
    </div>
    <Modal title="AI 补充答案" open={open} width={760} footer={null} onCancel={() => setOpen(false)}
      mask={{ closable: !busy }} keyboard={!busy} closable={!busy}>
      <Space orientation="vertical" size="middle" className="ai-answer-panel">
        <Typography.Paragraph className="ai-answer-intro">
          先生成草稿，核对后再确认采用。AI 来源会一直保留，确认后也不会覆盖资料原文或手写答案。
        </Typography.Paragraph>
        {(viewingPrevious || (confirmedRunId && confirmedRunId !== latestRun?.id)) && <Space wrap>
          <Button type={!viewingPrevious ? 'primary' : 'default'} disabled={busy} onClick={() => selectRun(null)}>最新生成记录</Button>
          {confirmedRunId && confirmedRunId !== latestRun?.id && <Button type={selectedRunId === confirmedRunId ? 'primary' : 'default'} disabled={busy}
            onClick={() => selectRun(confirmedRunId)}>当前已确认的 AI 答案</Button>}
        </Space>}
        {confirmedAiAnswerId === undefined && question.isError && <Alert type="error" showIcon title="已确认的 AI 版本加载失败" description={question.error.message}
          action={<Button onClick={() => void question.refetch()}>重新加载</Button>} />}
        {viewingPrevious && <Typography.Text type="secondary">正在查看此前生成的版本。最新草稿另行保留，确认后才会参与有效答案选择。</Typography.Text>}
        {visibleQuery.isFetching && !run && <Skeleton active paragraph={{ rows: 2 }} />}
        {visibleQuery.isError && <Alert type="error" showIcon title="AI 补充记录加载失败" description={visibleQuery.error.message}
          action={<Button onClick={() => void visibleQuery.refetch()}>重新加载</Button>} />}
        {actionError && <Alert type="error" showIcon title={actionError} />}
        {success && <Alert type="success" showIcon title={success} />}
        {stale && <Alert type="warning" showIcon title="这份 AI 补充的题目或上下文已变化"
          description="原内容保留供查看，请按当前信息重新生成后核对。" />}
        {isPending && <Alert type="info" showIcon title={run?.status === 'QUEUED' ? '已加入生成队列' : '正在生成 AI 草稿'}
          description="可以关闭窗口继续使用，生成结果会保留在这道题下。" />}
        {run?.status === 'FAILED' && run.reviewStatus !== 'DISCARDED' && <Alert type="error" showIcon title="AI 补充生成失败"
          description={run.errorMessage || '请稍后重试。'} />}
        {run && (isPending || run.canRetry) && <Space wrap>
          {run.canRetry && !stale && <Button loading={retry.isPending} disabled={busy || disabled || visibleQuery.isFetching}
            onClick={() => retry.mutate()}>{isPending ? '重新提交中断任务' : '重试'}（{run.retryCount}/{run.retryLimit}）</Button>}
          {isPending && <Button loading={review.isPending} disabled={busy || disabled}
            onClick={() => review.mutate({ action: 'DISCARD' })}>取消本次生成</Button>}
        </Space>}
        {hasContent && run && <section className="ai-answer-content" aria-label="AI 补充答案内容">
          <div className="ai-answer-content-heading">
            <Typography.Text strong>AI 补充答案</Typography.Text>
            <Tag color={run.reviewStatus === 'CONFIRMED' ? 'blue' : 'gold'}>{run.reviewStatus === 'CONFIRMED' ? '已确认 · AI 来源' : '草稿 · 待核对'}</Tag>
          </div>
          {editing ? <Input.TextArea aria-label="编辑 AI 补充答案" value={draft} onChange={(event) => setDraft(event.target.value)} rows={14} maxLength={12000} showCount />
            : <SafeMarkdown className="answer-text">{run.content!}</SafeMarkdown>}
          {editing && run.reviewStatus === 'CONFIRMED' && <Typography.Paragraph type="secondary">
            保存草稿会将这份答案改为待核对；选择“保存并确认采用”可直接使用修改后的内容。
          </Typography.Paragraph>}
          <Typography.Paragraph type="secondary" className="ai-answer-meta">
            {run.basis === 'SOURCE_ASSISTED' ? '结合本题资料片段生成' : '基于模型通用知识生成，无资料引用'}
            {run.model ? ` · ${run.model}` : ''}
          </Typography.Paragraph>
          {run.citations.length > 0 && <details className="ai-answer-citations"><summary>查看引用片段（{run.citations.length}）</summary>
            {run.citations.map((citation, index) => <blockquote key={`${citation.chunkId}-${index}`}>{citation.quote}</blockquote>)}
          </details>}
          {!stale && !disabled && <Space wrap className="ai-answer-actions">
            {editing ? <>
              <Button type="primary" loading={review.isPending} disabled={busy || !draft.trim()}
                onClick={() => review.mutate({ action: 'CONFIRM', content: draft.trim() })}>保存并确认采用</Button>
              <Button disabled={busy || !draft.trim()} onClick={() => review.mutate({ action: 'SAVE_DRAFT', content: draft.trim() })}>保存草稿</Button>
              <Button disabled={busy} onClick={() => setEditing(false)}>取消编辑</Button>
            </> : <>
              {run.reviewStatus === 'DRAFT' && <Button type="primary" loading={review.isPending} disabled={busy}
                onClick={() => review.mutate({ action: 'CONFIRM' })}>已核对，确认采用</Button>}
              <Button disabled={busy} onClick={() => { setDraft(run.content!); setEditing(true); setNewDraftRequested(false); setSuccess(null) }}>编辑 AI 补充</Button>
              <Button disabled={busy} onClick={() => review.mutate({ action: 'DISCARD' })}>{run.reviewStatus === 'CONFIRMED' ? '停止采用' : '丢弃草稿'}</Button>
              {!newDraftRequested && <Button disabled={busy || query.isFetching} onClick={() => { setNewDraftRequested(true); setSuccess(null) }}>再生成一份草稿</Button>}
            </>}
          </Space>}
        </section>}
        {canGenerate && !isPending && !pending(latestRun) && !visibleQuery.isLoading && !visibleQuery.isError && !query.isError && <div className="ai-answer-generation">
          {newDraftRequested && <Typography.Paragraph type="secondary">新稿需要再次核对。已有的有效确认版会继续保留，直到你确认采用新稿。</Typography.Paragraph>}
          <Space orientation="vertical" size="small">
            <Checkbox checked={useProfile} onChange={(event) => setUseProfile(event.target.checked)} disabled={busy}>结合目标岗位、已确认技能与项目</Checkbox>
            <Checkbox checked={includeNote} onChange={(event) => setIncludeNote(event.target.checked)} disabled={busy}>包含本题私人备注</Checkbox>
          </Space>
          <Typography.Paragraph type="secondary">
            点击后会把当前问题、必要资料片段、筛选偏好及所选上下文发送到已配置的模型，可能产生 API 费用。不会发送完整简历；备注仅在勾选时发送。
          </Typography.Paragraph>
          <Button type="primary" loading={generate.isPending} disabled={busy || disabled || query.isFetching || visibleQuery.isFetching}
            onClick={() => generate.mutate()}>{run ? '按当前信息重新生成' : '生成 AI 草稿'}</Button>
          {newDraftRequested && <Button type="text" disabled={busy} onClick={() => setNewDraftRequested(false)}>取消重新生成</Button>}
        </div>}
      </Space>
    </Modal>
  </>
}
