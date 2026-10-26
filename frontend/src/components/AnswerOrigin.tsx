import { Space, Tag, Typography } from 'antd'
import type { AnswerSourceType } from '../api/aiAnswers'

const answerOriginLabel = (sourceType: AnswerSourceType | null | undefined) => {
  if (sourceType === 'AI_GENERATED') return 'AI 补充答案'
  if (sourceType === 'USER_WRITTEN') return '人工答案'
  if (sourceType === 'SOURCE_EXTRACTED') return '资料原文答案'
  return '参考答案'
}

export function AnswerOrigin({ sourceType }: { sourceType: AnswerSourceType | null | undefined }) {
  return <Space wrap size="small">
    <Typography.Text strong>{answerOriginLabel(sourceType)}</Typography.Text>
    {sourceType === 'AI_GENERATED' && <Tag color="blue">用户已确认 · AI 来源</Tag>}
  </Space>
}
