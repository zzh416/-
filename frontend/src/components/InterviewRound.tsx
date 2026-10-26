import { useMutation } from '@tanstack/react-query'
import { Alert, Button, Card, Input, Space, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'

import { fetchInterview, interviewAction, saveInterviewDraft, submitInterviewAnswer, type InterviewSession } from '../api/interviews'
import { InterviewFeedback } from './InterviewReport'
import { SafeMarkdown } from './SafeMarkdown'
import { SourceStatistics } from './SourceStatistics'

interface Props { session: InterviewSession; onUpdate: (next: InterviewSession) => void; notify: (kind: 'success' | 'error', text: string) => void }

export function InterviewRound({ session, onUpdate, notify }: Props) {
  const turn = session.turns[session.turns.length - 1]
  const cacheKey = `interview-draft:${session.id}:${turn.id}`
  const [text, setText] = useState(() => sessionStorage.getItem(cacheKey) ?? turn.draft)
  const [clock, setClock] = useState(() => Date.parse(session.serverNow))
  const [conflict, setConflict] = useState(false)
  useEffect(() => {
    const offset = Date.parse(session.serverNow) - Date.now()
    const timer = window.setInterval(() => setClock(Date.now() + offset), 500)
    return () => window.clearInterval(timer)
  }, [session.serverNow])
  const serverClock = Math.max(clock, Date.parse(session.serverNow))
  const remaining = session.status === 'ACTIVE' && turn.deadlineAt ? Math.max(0, Math.ceil((Date.parse(turn.deadlineAt) - serverClock) / 1000)) : Math.ceil(turn.remainingSeconds)
  const onError = (error: Error) => { setConflict(true); notify('error', error.message); void fetchInterview(session.id).then(onUpdate).catch(() => notify('error', '无法刷新状态，草稿仍保留在当前标签页')) }
  const draft = useMutation({ mutationFn: () => saveInterviewDraft(session.id, { expectedVersion: session.version, turnId: turn.id, text }), onSuccess: onUpdate, onError })
  const action = useMutation({ mutationFn: (value: string) => interviewAction(session.id, { expectedVersion: session.version, turnId: turn.id, action: value }), onSuccess: onUpdate, onError })
  const answer = useMutation({ mutationFn: (value: 'ANSWER' | 'DONT_KNOW' | 'SKIP') => submitInterviewAnswer(session.id, { expectedVersion: session.version, turnId: turn.id, text, action: value }),
    onSuccess: (next) => { sessionStorage.removeItem(cacheKey); onUpdate(next) }, onError })
  const busy = draft.isPending || action.isPending || answer.isPending
  const saveDraft = draft.mutate
  useEffect(() => {
    if (conflict || busy || text === turn.draft || !['ACTIVE', 'PAUSED'].includes(session.status)) return
    const timer = window.setTimeout(() => saveDraft(), 800)
    return () => window.clearTimeout(timer)
  }, [text, turn.draft, session.version, session.status, conflict, busy, saveDraft])
  const active = session.status === 'ACTIVE'
  return <Space orientation="vertical" size="middle" className="profile-stack">
    <Card>
      <Space wrap>
        <Tag>{turn.kind === 'BANK' ? '题库已有题' : turn.kind === 'AI_FOLLOWUP' ? 'AI 追问题' : 'AI 自主题'}</Tag>
        {turn.skillBasis && <Tag>技能依据：{turn.skillBasis}</Tag>}
        {turn.projectName && <Tag>项目依据：{turn.projectName}</Tag>}
        <Tag>{turn.depth ? `追问 ${turn.depth}/2` : `基础题 ${session.plan.findIndex((item) => item.id === turn.rootId) + 1}/${session.plan.length}`}</Tag>
        {['ACTIVE', 'PAUSED'].includes(session.status) && <Typography.Text strong role="timer" aria-label="本题剩余时间">剩余 {Math.floor(remaining / 60)}:{String(remaining % 60).padStart(2, '0')}</Typography.Text>}
      </Space>
      <SafeMarkdown className="recommendation-question">{turn.prompt ?? ''}</SafeMarkdown>
      <details className="question-source-statistics"><summary>出题原因与来源</summary><Typography.Paragraph>{turn.reason}</Typography.Paragraph><SourceStatistics statistics={turn.sourceStatistics} />{turn.projectQuote && <blockquote>{turn.projectQuote}</blockquote>}{turn.questionQuote && <><Typography.Text>原题依据：</Typography.Text><blockquote>{turn.questionQuote}</blockquote></>}{turn.answerQuote && <><Typography.Text>本次追问引用你上题的回答：</Typography.Text><blockquote>{turn.answerQuote}</blockquote></>}</details>
    </Card>
    {['ACTIVE', 'PAUSED'].includes(session.status) && <>
      {active && remaining === 0 && <Alert type="warning" showIcon title="本题已到时，文字不会自动提交" description="请选择提交当前回答、加时1分钟，或不会／换方向。" />}
      {session.status === 'PAUSED' && <Alert type="info" showIcon title="已暂停计时，恢复后继续作答" />}
      <Input.TextArea aria-label="模拟面试回答" rows={8} maxLength={12000} showCount value={text} disabled={!active || remaining === 0 || action.isPending || answer.isPending}
        onChange={(event) => { setText(event.target.value); sessionStorage.setItem(cacheKey, event.target.value); setConflict(false) }} placeholder="用文字说明你的思路、实现和依据……" />
      <Typography.Text type="secondary">{conflict ? '草稿尚未同步，请先保留文字并点击保存草稿重试' : draft.isPending || text !== turn.draft ? '草稿正在保存…' : '草稿已保存'} · 关闭面板不会暂停计时，请先手动暂停。</Typography.Text>
      <Space wrap>
        <Button type="primary" loading={answer.isPending} disabled={busy || !active || !text.trim()} onClick={() => answer.mutate('ANSWER')}>提交回答</Button>
        <Button danger disabled={busy || !active} onClick={() => answer.mutate('DONT_KNOW')}>不会／不了解，换方向</Button>
        <Button disabled={busy || !active} onClick={() => answer.mutate('SKIP')}>跳过此题</Button>
        <Button disabled={busy || turn.allocatedSeconds >= 900} onClick={() => action.mutate('EXTEND')}>加时1分钟</Button>
        <Button disabled={busy} onClick={() => action.mutate(active ? 'PAUSE' : 'RESUME')}>{active ? '暂停' : '恢复计时'}</Button>
        {conflict && <Button loading={draft.isPending} onClick={() => { setConflict(false); draft.mutate() }}>保存草稿</Button>}
      </Space>
    </>}
    {session.status === 'REVIEWING' && <><Alert type="info" showIcon title="回答已保存，正在生成反馈；此阶段不计回答时间" /><Button disabled={busy} onClick={() => action.mutate('NEXT')}>不等反馈，下一题</Button>{session.canRetry && <Button disabled={busy} onClick={() => action.mutate('RETRY_REVIEW')}>重试反馈</Button>}</>}
    {session.status === 'AWAITING_NEXT' && <>
      {turn.reviewError && <Alert type="warning" showIcon title={turn.reviewError} />}
      <InterviewFeedback turn={turn} />
      {turn.followup && <Card size="small" title="可选追问（AI 建议）"><SafeMarkdown>{turn.followup.prompt}</SafeMarkdown></Card>}
      <Space wrap>{turn.followup && <Button type="primary" disabled={busy} onClick={() => action.mutate('FOLLOWUP')}>继续追问</Button>}<Button disabled={busy} onClick={() => action.mutate('NEXT')}>下一题</Button>{session.canRetry && <Button disabled={busy || turn.reviewAttempts >= 3} onClick={() => action.mutate('RETRY_REVIEW')}>重试反馈</Button>}</Space>
    </>}
  </Space>
}
