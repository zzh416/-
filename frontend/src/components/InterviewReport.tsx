import { Card, Space, Tag, Typography } from 'antd'

import type { InterviewSession, InterviewTurn } from '../api/interviews'
import { SafeMarkdown } from './SafeMarkdown'

export function InterviewFeedback({ turn }: { turn: InterviewTurn }) {
  if (!turn.feedback) return null
  return <Card size="small" title="模型反馈（供复盘，需人工核对）">
    {turn.referenceAnswerSourceType === 'AI_GENERATED' && <Typography.Paragraph type="secondary">本次反馈参考了已确认的 AI 补充答案；AI 来源仅供复习参考。</Typography.Paragraph>}
    <Typography.Paragraph>{turn.feedback.summary}</Typography.Paragraph>
    {turn.feedback.coveredPoints.length > 0 && <><Typography.Text strong>已覆盖</Typography.Text><ul>{turn.feedback.coveredPoints.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
    {turn.feedback.gaps.length > 0 && <><Typography.Text strong>待澄清／补充</Typography.Text><ul>{turn.feedback.gaps.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
    <Typography.Text type="secondary">没有可靠参考内容时，反馈侧重表达和覆盖点，不代表技术正确率。</Typography.Text>
  </Card>
}

const answerLabels = { DRAFT: '未提交草稿', ANSWERED: '已作答', DONT_KNOW: '暂时不会', SKIPPED: '已跳过' }

export function InterviewReport({ session }: { session: InterviewSession }) {
  return <Space orientation="vertical" size="middle" className="profile-stack">
    <Card title="本轮复盘">
      <Typography.Paragraph>{session.finishedReason}</Typography.Paragraph>
      <Space wrap><Tag>已作答 {session.turns.filter((turn) => turn.answerStatus === 'ANSWERED').length}</Tag><Tag>暂时不会 {session.turns.filter((turn) => turn.answerStatus === 'DONT_KNOW').length}</Tag><Tag>跳过同主题题 {session.skippedPlanCount}</Tag></Space>
      {session.blindTopics.length > 0 && <Typography.Paragraph>待复习方向：{session.blindTopics.join('、')}</Typography.Paragraph>}
    </Card>
    {session.turns.map((turn, index) => <Card key={turn.id} size="small" title={`记录 ${index + 1} · ${turn.depth ? '追问' : '基础题'} · ${answerLabels[turn.answerStatus]}`}>
      <Space wrap><Tag>{turn.kind === 'BANK' ? '题库已有题' : turn.kind === 'AI_FOLLOWUP' ? 'AI 追问题' : 'AI 自主题'}</Tag>{turn.skillBasis && <Tag>技能依据：{turn.skillBasis}</Tag>}{turn.projectName && <Tag>项目依据：{turn.projectName}</Tag>}</Space>
      <SafeMarkdown>{turn.prompt ?? ''}</SafeMarkdown>
      <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{turn.answer || turn.draft || '未填写回答'}</Typography.Paragraph>
      <Typography.Paragraph type="secondary">作答用时 {turn.elapsedSeconds} 秒{turn.timedOut ? ' · 已到时' : ''}</Typography.Paragraph>
      {turn.reviewError && <Typography.Paragraph type="secondary">{turn.reviewError}</Typography.Paragraph>}
      <InterviewFeedback turn={turn} />
    </Card>)}
  </Space>
}
