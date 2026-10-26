import { Alert, Space, Typography } from 'antd'
import type { InterviewSession } from '../api/interviews'

const labels: Record<string, string> = {
  SCHEMA: '条目格式不合规', DUPLICATE: '重复题', QUESTION_FILTER: '技能、排除或题目质量校验未通过',
  BASIS_SCOPE: '不符合所选出题范围', BASIS_PROJECT: '项目不在所选范围', BASIS_QUOTE: '项目引文未逐字匹配',
  BASIS_SKILL: '技能尚未确认', BASIS_TOPIC: '题目与依据主题不匹配', BASIS_CONFLICT: '项目与技能依据冲突',
  UNKNOWN_CANDIDATE: '无效题库题目编号', WRONG_SOURCE: '与所选出题来源不一致', EMPTY_RESPONSE: '模型未返回题目',
}
const errorLabels: Record<string, string> = {
  INTERVIEW_INVALID_RESPONSE: '模型返回结构或JSON格式不符合约定',
  INTERVIEW_OUTPUT_TRUNCATED: '模型输出被截断', INTERVIEW_MODEL_UNAVAILABLE: '模型服务暂时不可用',
  INTERVIEW_MODEL_AUTH_FAILED: '模型鉴权失败', INTERVIEW_RESOURCE_EXHAUSTED: '模型余额或资源不足',
  INTERVIEW_MODEL_NOT_CONFIGURED: '文本模型尚未配置',
}

export function InterviewPlanningDetails({ session }: { session: InterviewSession }) {
  const data = session.planningDiagnostics
  if (!data) return null
  return <Space orientation="vertical" size="small" className="profile-stack">
    {session.status === 'PLANNING' && <Alert type="info" showIcon
      title={`正在准备题目：已保留 ${data.acceptedCount}/${data.targetCount} 道`}
      description={`本次已请求模型 ${data.modelCalls}/3 轮；不足时保留有效题继续补齐，未通过的题不会用于凑数。`} />}
    <details className="question-source-statistics">
      <summary>出题检查详情</summary>
      <Typography.Paragraph>目标 {data.targetCount} 道 · 已保留 {data.acceptedCount} 道 · 本次模型请求 {data.modelCalls} 轮</Typography.Paragraph>
      <Typography.Paragraph type="secondary">模型累计返回 {data.returnedCount} 条候选，可能包含重复和无效项；格式问题 {data.formatIssueCount} 处。</Typography.Paragraph>
      {session.questionSource === 'HYBRID' && <Typography.Paragraph type="secondary">混合模式也会使用已有题库候选补齐，保留数可能大于模型返回条目数。</Typography.Paragraph>}
      {Object.keys(data.rejectionCounts).length > 0 && <ul>{Object.entries(data.rejectionCounts).map(([code, count]) => <li key={code}>{labels[code] ?? '其他校验问题'}：{count}</li>)}</ul>}
      {data.errors.length > 0 && <Typography.Paragraph type="secondary">本次遇到：{[...new Set(data.errors)].map((code) => errorLabels[code] ?? '模型处理问题').join('、')}。可修复的格式问题已在本次次数限制内重试。</Typography.Paragraph>}
      {data.exhausted && <Typography.Text type="warning">本次自动补题结束后仍有缺额，可手动继续补足，或开始现有题目。</Typography.Text>}
    </details>
  </Space>
}
