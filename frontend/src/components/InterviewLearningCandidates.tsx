import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Space, Tag, Typography } from 'antd'
import { confirmLearningCandidate, fetchLearningCandidates } from '../api/learning'
import { learningOptions } from './learningOptions'
import { SafeMarkdown } from './SafeMarkdown'

export function InterviewLearningCandidates({ sessionId, sessionVersion, enabled }: { sessionId: string; sessionVersion: number; enabled: boolean }) {
  const queryClient = useQueryClient()
  const query = useQuery({ queryKey: ['learning', 'candidates', sessionId, sessionVersion], queryFn: () => fetchLearningCandidates(sessionId), enabled })
  const confirm = useMutation({ mutationFn: (turnId: string) => confirmLearningCandidate(sessionId, turnId), onSettled: () => queryClient.invalidateQueries({ queryKey: ['learning'] }) })
  if (query.isError) return <Alert type="error" showIcon title="不会题确认列表加载失败" description={query.error.message} action={<Button onClick={() => void query.refetch()}>重试</Button>} />
  if (!query.data?.items.length) return null
  return <Card title={`本轮不会题待确认（${query.data.items.filter((i) => !i.confirmed).length}）`}>
    <Space orientation="vertical" size="middle" className="profile-stack">
      <Typography.Paragraph type="secondary">点击确认后才加入“薄弱题复习”并标记为不会；若原题已标记已掌握，本次确认会改成不会。题库题关联原题，AI题与追问独立保存，不写入资料题库。</Typography.Paragraph>
      {confirm.isError && <Alert type="error" title={confirm.error.message} />}
      {query.data.items.map((item) => <Card key={item.turnId} size="small">
        <Space orientation="vertical" size="small" className="profile-stack">
          <Tag color={item.sourceKind === 'BANK' ? 'blue' : 'purple'}>{item.sourceKind === 'BANK' ? '题库已有题' : '面试生成题'}</Tag>
          <SafeMarkdown className="question-text">{item.prompt}</SafeMarkdown>
          {item.confirmed ? <Tag color="success">已确认 · 当前{learningOptions.find((option) => option.value === item.learning?.status)?.label ?? '记录已移除'}</Tag>
            : <Button type="primary" disabled={!item.canConfirm || confirm.isPending} loading={confirm.isPending && confirm.variables === item.turnId} onClick={() => confirm.mutate(item.turnId)}>确认标记为不会，加入复习</Button>}
          {item.message && <Typography.Text type="secondary">{item.message}</Typography.Text>}
        </Space>
      </Card>)}
    </Space>
  </Card>
}
