import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Drawer, Empty, Popconfirm, Select, Skeleton, Space, Typography } from 'antd'
import { useState } from 'react'

import { fetchLatestRecommendationRun, fetchProfile } from '../api/client'
import { createInterview, fetchInterview, fetchInterviewHistory, interviewAction, type InterviewCreate, type InterviewSession } from '../api/interviews'
import { InterviewRound } from './InterviewRound'
import { InterviewReport } from './InterviewReport'
import { SafeMarkdown } from './SafeMarkdown'
import { InterviewLearningCandidates } from './InterviewLearningCandidates'
import { InterviewSetup } from './InterviewSetup'
import { sourceLabels } from './interviewSources'
import { InterviewPlanningDetails } from './InterviewPlanningDetails'

interface Props { open: boolean; onClose: () => void; notify: (kind: 'success' | 'error', text: string) => void; initialSessionId?: string; onOpenProfile: () => void }
const labels: Record<InterviewSession['status'], string> = { PLANNING: 'AI 正在出题', PLAN_FAILED: '出题失败', READY: '计划已准备', ACTIVE: '正在作答', PAUSED: '已暂停', REVIEWING: '生成反馈', AWAITING_NEXT: '等待选择下一步', COMPLETED: '本轮结束' }

export function InterviewDrawer({ open, onClose, notify, initialSessionId, onOpenProfile }: Props) {
  const queryClient = useQueryClient()
  const [activeId, setActiveId] = useState<string | null>(initialSessionId ?? null)
  const [creating, setCreating] = useState(false)
  const history = useQuery({ queryKey: ['interview-history'], queryFn: fetchInterviewHistory, enabled: open })
  const recommendation = useQuery({ queryKey: ['recommendation-run', 'latest'], queryFn: fetchLatestRecommendationRun, enabled: open })
  const profile = useQuery({ queryKey: ['profile'], queryFn: fetchProfile, enabled: open })
  const id = activeId ?? history.data?.items[0]?.id
  const current = useQuery({ queryKey: ['interview', id], queryFn: () => fetchInterview(id!), enabled: open && Boolean(id), refetchInterval: (query) => ['PLANNING', 'REVIEWING'].includes(query.state.data?.status ?? '') ? 1500 : false })
  const update = (next: InterviewSession) => queryClient.setQueryData<InterviewSession>(['interview', next.id], (previous) => previous && previous.version > next.version ? previous : next)
  const onError = (error: Error) => { notify('error', error.message); void queryClient.invalidateQueries({ queryKey: ['interview-history'] }); void current.refetch() }
  const create = useMutation({ mutationFn: (values: InterviewCreate) => createInterview(values), onSuccess: (next) => {
    update(next); setActiveId(next.id); setCreating(false); void queryClient.invalidateQueries({ queryKey: ['interview-history'] })
  }, onError })
  const action = useMutation({ mutationFn: (value: string) => interviewAction(id!, { expectedVersion: current.data!.version, action: value }), onSuccess: update, onError })
  const session = current.data
  const setup = creating || (!history.isLoading && !id)
  return <Drawer title="文字模拟面试" size={820} open={open} onClose={onClose}>
    <Space orientation="vertical" size="middle" className="profile-stack">
      <Alert type="info" showIcon title="逐题作答，是否追问由你决定" description="文字默认每题3分钟，可调整；不会时立即切换方向。AI 出题／反馈可能产生 API 费用，会发送必要岗位技能、项目、题目、筛选约束、作答及最多3000字符的已有适用参考片段；不发送完整简历或私人备注，不生成参考答案。" />
      {(history.isLoading || current.isLoading) && <Skeleton active />}
      {history.isError && <Alert type="error" title={history.error.message} />}
      {current.isError && <Alert type="error" title={current.error.message} />}
      {Boolean(history.data?.items.length) && <Select aria-label="模拟面试历史" style={{ width: '100%' }} value={id} onChange={(value) => { setActiveId(value); setCreating(false) }} options={history.data?.items.map((item) => ({ value: item.id, label: `${new Date(item.createdAt).toLocaleString()} · ${labels[item.id === id && session ? session.status : item.status]}` }))} />}
      {!setup && session?.status === 'COMPLETED' && <Button type="primary" onClick={() => setCreating(true)}>新建一轮面试</Button>}
      {setup && profile.isError && <Alert type="error" title="画像加载失败" description={profile.error.message} action={<Button onClick={() => void profile.refetch()}>重试</Button>} />}
      {setup && recommendation.isError && <Alert type="warning" title="题库清单加载失败，仍可选择AI自主出题" description={recommendation.error.message} />}
      {setup && (profile.isLoading || recommendation.isLoading ? <Skeleton active /> : <InterviewSetup run={recommendation.data} profile={profile.data} pending={create.isPending} onSubmit={(input) => create.mutate(input)} onBack={id ? () => setCreating(false) : undefined} onOpenProfile={onOpenProfile} />)}
      {!setup && session && <>
        <Typography.Text strong>{labels[session.status]} · {sourceLabels[session.questionSource]} · 本轮默认每题 {session.questionSeconds} 秒</Typography.Text>
        {!session.isCurrent && <Alert type="warning" title="本轮依据已变化，记录仍保留；请结束后重新建立面试。" />}
        {session.notice && <Alert type="info" showIcon title={session.notice} />}
        {session.planningError && <Alert type="warning" showIcon title={session.planningError} />}
        <InterviewPlanningDetails session={session} />
        {['PLANNING', 'PLAN_FAILED'].includes(session.status) && <Space wrap>{session.questionSource !== 'AI' && <Button loading={action.isPending} onClick={() => action.mutate('USE_BANK')}>改用已有题库清单</Button>}{session.canRetry && <Button loading={action.isPending} onClick={() => action.mutate('RETRY_PLAN')}>重试AI出题</Button>}</Space>}
        {session.status === 'READY' && <>
          {session.questionSource !== 'BANK' && session.plan.length < session.questionCount && <Button
            loading={action.isPending} disabled={!session.isCurrent || session.planAttempts >= 3}
            onClick={() => action.mutate('SUPPLEMENT_PLAN')}>补足目标题数（{session.plan.length}/{session.questionCount}）</Button>}
          <Typography.Paragraph>已准备 {session.plan.length} 道基础题，追问由你逐次选择；点击开始后启动本题计时。</Typography.Paragraph>
          {session.mode === 'PREVIEW' && session.plan.map((item, index) => <Card key={item.id} size="small" title={`${index + 1}. ${item.kind === 'BANK' ? '题库已有题' : 'AI 自主题'}`}><SafeMarkdown>{item.prompt ?? ''}</SafeMarkdown><Typography.Paragraph type="secondary">{item.reason}</Typography.Paragraph>{item.skillBasis && <Typography.Text>技能依据：{item.skillBasis}</Typography.Text>}{item.projectName && <Typography.Text>项目依据：{item.projectName}</Typography.Text>}</Card>)}
          <Button type="primary" loading={action.isPending} onClick={() => action.mutate('START')}>开始逐题面试</Button>
        </>}
        {session.turns.length > 0 && session.status !== 'COMPLETED' && <InterviewRound key={`${session.id}:${session.turns[session.turns.length - 1].id}`} session={session} onUpdate={update} notify={notify} />}
        {session.status === 'COMPLETED' ? <InterviewReport session={session} /> : <Popconfirm title="结束本轮面试？" description="已保存的草稿、回答和反馈会保留。" onConfirm={() => action.mutate('FINISH')}><Button danger loading={action.isPending}>结束本轮面试</Button></Popconfirm>}
      </>}
      {!setup && session && <InterviewLearningCandidates sessionId={session.id} sessionVersion={session.version} enabled={open} />}
      {!setup && !session && !current.isLoading && <Empty description="请选择一条面试记录" />}
    </Space>
  </Drawer>
}
