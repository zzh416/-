import { Alert, Card, Space, Tag, Typography } from 'antd'

import type { RecommendationItem } from '../api/client'
import { SafeMarkdown } from './SafeMarkdown'
import { SourceStatistics } from './SourceStatistics'


interface RecommendationItemCardProps {
  item: RecommendationItem
}

const technologyTags = (
  label: string,
  items: RecommendationItem['questionTechnologies'],
) => {
  if (items.length === 0) return null
  return (
    <div className="recommendation-technologies">
      <Typography.Text type="secondary">{label}</Typography.Text>
      <Space size={[4, 4]} wrap>
        {items.map((item) => (
          <Tag key={item.canonicalName}>{item.displayName}</Tag>
        ))}
      </Space>
    </div>
  )
}

export function RecommendationItemCard({ item }: RecommendationItemCardProps) {
  const answerUnavailable = item.answerState === 'SOURCE_ANSWER' && item.usableAnswerState === 'MISSING'
  return (
    <Card className={`recommendation-card recommendation-${item.decision.toLowerCase()}`}>
      <div className="recommendation-card-heading">
        <Space wrap>
          <Tag color={item.decision === 'INCLUDED' ? 'blue' : 'default'}>
            {item.decision === 'INCLUDED' ? '进入清单' : '已排除'}
          </Tag>
          {item.decision === 'INCLUDED' && (
            <Tag color={item.usableAnswerState === 'ANSWERED' ? 'green' : 'gold'}>
              {item.usableAnswerState === 'ANSWERED' ? '有可用答案' : '当前无可用答案'}
            </Tag>
          )}
          <Typography.Text type="secondary">相关度 {Math.round(item.score)}</Typography.Text>
        </Space>
      </div>

      <SafeMarkdown className="recommendation-question">{item.prompt}</SafeMarkdown>
      <SourceStatistics statistics={item.sourceStatistics} />

      {item.answerContent && item.decision === 'INCLUDED' && (
        <div className={answerUnavailable ? 'recommendation-answer is-unavailable' : 'recommendation-answer'}>
          <Typography.Text strong>资料答案</Typography.Text>
          {answerUnavailable && (
            <Alert
              type="warning"
              showIcon
              title="资料中有答案，但答案使用了当前排除或不匹配的技术"
            />
          )}
          <SafeMarkdown>{item.answerContent}</SafeMarkdown>
        </div>
      )}

      <div className="recommendation-reasons" aria-label="筛选依据">
        <Typography.Text strong>筛选依据</Typography.Text>
        <ul>
          {item.reasons.map((reason, index) => (
            <li key={`${item.reasonCodes[index] ?? 'reason'}-${index}`}>{reason}</li>
          ))}
        </ul>
      </div>

      {technologyTags('题干技术', item.questionTechnologies)}
      {technologyTags('答案技术', item.answerTechnologies)}
    </Card>
  )
}
