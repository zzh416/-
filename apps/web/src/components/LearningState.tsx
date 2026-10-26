import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Select, Space, Tag, Typography } from 'antd'
import { createContext, useContext, type ReactNode } from 'react'
import { lookupLearning, updateLearning, type LearningState, type LearningStatus } from '../api/learning'
import { learningOptions } from './learningOptions'
const StateContext = createContext<{ states: Map<string, LearningState>; enabled: boolean }>({ states: new Map(), enabled: true })

export function LearningProvider({ questionIds, versionKey = '', enabled = true, children }: { questionIds: string[]; versionKey?: string; enabled?: boolean; children: ReactNode }) {
  const ids = [...new Set(questionIds)].sort()
  const query = useQuery({ queryKey: ['learning', 'lookup', ids, versionKey], queryFn: () => lookupLearning(ids), enabled: enabled && ids.length > 0 })
  const states = new Map(query.data?.items.flatMap((item) => item.questionId ? [[item.questionId, item] as const] : []) ?? [])
  return <StateContext.Provider value={{ states, enabled }}>
    {query.isError && <Alert type="error" showIcon title="复习状态加载失败" description={query.error.message} action={<Button onClick={() => void query.refetch()}>重试</Button>} />}
    {children}
  </StateContext.Provider>
}

export function LearningSelect({ item, disabled = false }: { item: LearningState; disabled?: boolean }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({ mutationFn: (status: LearningStatus) => updateLearning(item, status), onSettled: () => queryClient.invalidateQueries({ queryKey: ['learning'] }) })
  return <Space orientation="vertical" size={4}>
    <Space wrap>
      <Typography.Text type="secondary">我的掌握程度</Typography.Text>
      <Select aria-label="我的掌握程度" style={{ minWidth: 120 }} value={item.status} options={learningOptions}
        disabled={disabled || mutation.isPending} loading={mutation.isPending} onChange={(status) => mutation.mutate(status)} />
      {item.contentChanged && <Tag color="orange">内容已更新，请重新确认</Tag>}
      {item.contentChanged && <Button disabled={disabled} loading={mutation.isPending} onClick={() => mutation.mutate(item.status)}>确认当前掌握程度</Button>}
    </Space>
    {mutation.isError && <Typography.Text type="danger">{mutation.error.message}</Typography.Text>}
  </Space>
}

export function QuestionLearning({ questionId, questionVersion, answerVersion, aiAnswerId = null, aiAnswerVersion = null }: {
  questionId: string; questionVersion: number; answerVersion: number | null; aiAnswerId?: string | null; aiAnswerVersion?: number | null
}) {
  const context = useContext(StateContext)
  const item = context.states.get(questionId)
  if (!context.enabled) return <Typography.Text type="secondary">历史清单请重新生成后再标记掌握程度。</Typography.Text>
  if (!item) return <Typography.Text type="secondary">掌握程度尚未加载。</Typography.Text>
  const stale = item.questionVersion !== questionVersion || item.answerVersion !== answerVersion
    || (item.aiAnswerId ?? null) !== aiAnswerId || (item.aiAnswerVersion ?? null) !== aiAnswerVersion
  return <Space orientation="vertical" size={4}>
    <LearningSelect item={item} disabled={stale} />
    {stale && <Typography.Text type="warning">当前显示的题目或答案版本已变化，请刷新后标记。</Typography.Text>}
  </Space>
}
