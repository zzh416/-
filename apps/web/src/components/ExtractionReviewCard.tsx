import { Button, Checkbox, Popconfirm, Space, Tag, Typography } from 'antd'

import type { ExtractionReviewItem } from '../api/client'
import { SafeMarkdown } from './SafeMarkdown'

const statusMeta = {
  PENDING: { color: 'gold', label: '待复核' },
  CONFIRMED: { color: 'green', label: '已确认' },
  REJECTED: { color: 'red', label: '已拒绝' },
} as const

interface Props {
  item: ExtractionReviewItem
  busy: boolean
  selected: boolean
  onSelect: (item: ExtractionReviewItem, selected: boolean) => void
  onLocate: (item: ExtractionReviewItem) => void
  onEdit: (item: ExtractionReviewItem, kind: 'question' | 'answer') => void
  onDeleteAnswer: (item: ExtractionReviewItem) => void
  onHistory: (item: ExtractionReviewItem) => void
  onReview: (
    item: ExtractionReviewItem,
    action: 'CONFIRM' | 'REJECT' | 'RESET',
  ) => void
}

export function ExtractionReviewCard({ item, busy, selected, onSelect, onLocate, onEdit, onDeleteAnswer, onHistory, onReview }: Props) {
  const status = statusMeta[item.reviewStatus]
  return (
    <article className={`review-card review-card-${item.reviewStatus.toLowerCase()}`}>
      <div className="review-card-heading">
        <Space wrap>
          <Checkbox
            aria-label={`选择题目：${item.prompt}`}
            checked={selected}
            onChange={(event) => onSelect(item, event.target.checked)}
          />
          <Tag color={status.color}>{status.label}</Tag>
          <Tag>{item.answer ? '有原文答案' : '无答案'}</Tag>
          <Typography.Text type="secondary">
            置信度 {Math.round(item.confidence * 100)}%
          </Typography.Text>
        </Space>
        <Button size="small" onClick={() => onLocate(item)}>
          定位原文
        </Button>
      </div>

      <Typography.Text strong>问题</Typography.Text>
      <div className="review-question"><SafeMarkdown>{item.prompt}</SafeMarkdown></div>
      <Typography.Text strong>答案</Typography.Text>
      {item.answer ? (
        <div className="review-answer"><SafeMarkdown>{item.answer.content}</SafeMarkdown></div>
      ) : (
        <Typography.Paragraph type="secondary">原资料中未识别到答案</Typography.Paragraph>
      )}

      <div className="review-evidence">
        <Typography.Text type="secondary">原问题引文</Typography.Text>
        <blockquote>{item.questionQuote}</blockquote>
        {item.answerSpans.length > 0 && (
          <>
            <Typography.Text type="secondary">原答案引文</Typography.Text>
            {item.answerSpans.map((span, index) => (
              <blockquote key={`${span.sourceChunkId}-${span.start}-${index}`}>
                {span.quote}
              </blockquote>
            ))}
          </>
        )}
      </div>

      <Space wrap className="review-card-actions">
        <Button size="small" onClick={() => onEdit(item, 'question')}>修改问题</Button>
        <Button size="small" onClick={() => onEdit(item, 'answer')}>
          {item.answer ? '修改答案' : '补充答案'}
        </Button>
        {item.answer && (
          <Popconfirm
            title="删除这个答案？"
            description="题目会保留为无答案状态，可在修改历史中恢复。"
            onConfirm={() => onDeleteAnswer(item)}
          >
            <Button size="small" danger loading={busy}>删除答案</Button>
          </Popconfirm>
        )}
        <Button size="small" onClick={() => onHistory(item)}>修改历史</Button>
        {item.reviewStatus === 'PENDING' && (
          <>
            <Button
              size="small"
              type="primary"
              loading={busy}
              onClick={() => onReview(item, 'CONFIRM')}
            >
              确认正确
            </Button>
            <Popconfirm
              title="拒绝这条抽取结果？"
              description="拒绝后它会从普通题库隐藏，但复核记录仍会保留。"
              onConfirm={() => onReview(item, 'REJECT')}
            >
              <Button size="small" danger loading={busy}>拒绝</Button>
            </Popconfirm>
          </>
        )}
        {item.reviewStatus !== 'PENDING' && (
          <Button size="small" loading={busy} onClick={() => onReview(item, 'RESET')}>
            重新复核
          </Button>
        )}
      </Space>
    </article>
  )
}
