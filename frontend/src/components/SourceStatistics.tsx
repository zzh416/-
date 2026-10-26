import { Space, Tag, Typography } from 'antd'

import type { SourceStatistics as Statistics } from '../api/client'

export function SourceStatistics({ statistics }: { statistics: Statistics | null }) {
  if (!statistics) return <Typography.Paragraph type="secondary">历史清单未记录来源统计，重新生成后可查看。</Typography.Paragraph>
  if (statistics.sourceCount === 0) return <Typography.Paragraph type="secondary">当前没有有效资料来源，手写题或来源已移除的题目不计入来源统计。</Typography.Paragraph>
  return (
    <details className="question-source-statistics">
      <summary aria-label="展开题目来源统计">
        <Space wrap size={4}>
          <Tag>面经来源 {statistics.interviewSourceCount}</Tag>
          <Tag>知识库来源 {statistics.knowledgeSourceCount}</Tag>
          <span>查看来源详情</span>
        </Space>
      </summary>
      <Typography.Paragraph type="secondary">
        当前资料中共 {statistics.sourceCount} 个去重来源、{statistics.occurrenceCount} 条有效出现记录。
        近 180 天面经来源 {statistics.recentInterviewSourceCount} 个；日期未知 {statistics.unknownDateSourceCount} 个。
        {statistics.latestSourceDate && <>最新已知资料日期：{statistics.latestSourceDate}。</>}
        仅统计当前资料库内容，不代表全网热度或被问概率；不同网址的转载、同一文件的不同格式仍可能重复。
      </Typography.Paragraph>
      <ul>
        {statistics.sources.map((source) => (
          <li key={source.assetId}>
            <Tag>{source.purpose === 'INTERVIEW_EXPERIENCE' ? '面经' : '知识库'}</Tag>
            {source.sourceUrl ? <a href={source.sourceUrl} target="_blank" rel="noopener noreferrer">{source.title}</a> : <span>{source.title}</span>}
            {' · '}{source.sourceDate ?? '日期未知'}
            {source.duplicateAssetCount > 1 && ` · 合并 ${source.duplicateAssetCount} 份重复资料`}
          </li>
        ))}
      </ul>
    </details>
  )
}
