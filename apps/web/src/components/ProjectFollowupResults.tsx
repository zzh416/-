import { Alert, Card, Space, Tag, Typography } from 'antd'

import type { ProjectFollowupRun } from '../api/client'
import { SafeMarkdown } from './SafeMarkdown'
import { SourceStatistics } from './SourceStatistics'

export function ProjectFollowupResults({ run }: { run: ProjectFollowupRun }) {
  const rejected = Object.values(run.rejectionCounts).reduce((sum, count) => sum + count, 0)
  return (
    <Space orientation="vertical" size="middle" className="profile-stack">
      {!run.isCurrent && <Alert type="warning" showIcon title="这是一份历史追问结果" description="画像、偏好或题库已有变化。查看使用保存的项目和种子题；请先重新生成复习清单再生成新追问。" />}
      {run.errorMessage && <Alert type="error" showIcon title={run.errorMessage} />}
      {rejected > 0 && <Alert type="warning" showIcon title={`有 ${rejected} 条候选未通过依据、去重或筛选校验，未展示这些问题。`} />}
      <Typography.Paragraph type="secondary">
        项目：{run.projectSnapshot.name} · 模型：{run.model ?? '等待调用'} · {new Date(run.createdAt).toLocaleString()}
      </Typography.Paragraph>
      {run.seeds.map((seed, index) => (
        <Card key={seed.id} size="small" title={`已有题目 ${index + 1}`}>
          <SafeMarkdown className="recommendation-question">{seed.prompt}</SafeMarkdown>
          <SourceStatistics statistics={seed.sourceStatistics} />
          {run.items.filter((item) => item.seedItemId === seed.id).map((item) => (
            <Card key={item.id} size="small" className="profile-card" style={{ marginTop: 12, marginLeft: (item.level - 1) * 10 }}>
              <Space wrap><Tag color="purple">AI 推演 · 非原文题</Tag><Typography.Text strong>第 {item.level} 层追问</Typography.Text></Space>
              <SafeMarkdown className="recommendation-question">{item.question}</SafeMarkdown>
              <details className="question-source-statistics">
                <summary>查看双重依据：项目说明与种子题</summary>
                <Typography.Paragraph strong>项目说明依据</Typography.Paragraph>
                <blockquote style={{ whiteSpace: 'pre-wrap' }}>{item.projectQuote}</blockquote>
                <Typography.Paragraph strong>种子题依据</Typography.Paragraph>
                <blockquote style={{ whiteSpace: 'pre-wrap' }}>{item.seedQuote}</blockquote>
                <Typography.Text type="secondary">引用已校验为输入原文片段，追问本身是 AI 建议，需人工核对。</Typography.Text>
              </details>
            </Card>
          ))}
        </Card>
      ))}
    </Space>
  )
}
