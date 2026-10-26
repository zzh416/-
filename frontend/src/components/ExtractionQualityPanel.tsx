import { useQuery } from '@tanstack/react-query'
import { Alert, Skeleton, Typography } from 'antd'

import { fetchExtractionReview, type ExtractionQuality } from '../api/client'

export function ExtractionQualitySummary({ quality }: { quality: ExtractionQuality }) {
  const diagnostics = quality.diagnostics
  const metrics = [
    ['出现记录', quality.occurrenceCount],
    ['去重题目', quality.uniqueQuestionCount],
    ['有效题目', quality.activeQuestionCount],
    ['待复核记录', quality.pendingCount],
    ['已确认记录', quality.confirmedCount],
    ['已排除记录', quality.rejectedCount],
    ['原文有答案记录', quality.sourceAnsweredCount],
    ['原文无答案记录', quality.sourceMissingCount],
    ['疑似漏题', `${quality.suspectedMissesTruncated ? '至少 ' : ''}${quality.suspectedMissCount}`],
  ]
  return (
    <section className="extraction-quality-panel" aria-label="抽取质量概览">
      <Typography.Title level={4}>抽取质量概览</Typography.Title>
      {!quality.isCurrentVersion && <Alert type="warning" showIcon title="这是旧资料版本的抽取结果，请在当前版本重新整理后查看最新质量。" />}
      <dl className="extraction-quality-metrics">
        {metrics.map(([label, count]) => <div key={label}><dt>{label}</dt><dd>{count}</dd></div>)}
      </dl>
      <Typography.Paragraph type="secondary">统计覆盖本次运行的全部记录，不随分页或复核筛选改变。题目可有多次出处；有答案/无答案按未排除记录的原文答案统计，人工修改后的答案请在题库查看。疑似漏题只是规则提示，不是已确认遗漏，也不能用于计算召回率。</Typography.Paragraph>
      {diagnostics ? (
        <Typography.Paragraph type="secondary">
          最近一次尝试：共 {diagnostics.plannedBatchCount} 批，成功返回 {diagnostics.completedBatchCount} 批，失败 {diagnostics.failedBatchCount} 批；
          模型返回 {diagnostics.modelCandidateCount} 条候选；来源校验未通过 {diagnostics.invalidSourceCount ?? '尚未校验'} 条，重复原文范围 {diagnostics.duplicateRangeCount ?? '尚未校验'} 条。
          重试前已保存的有效结果仍保留。
        </Typography.Paragraph>
      ) : <Typography.Paragraph type="secondary">此历史任务没有记录批次与候选诊断，显示为未知；不会根据结果数量推算模型返回了多少题。</Typography.Paragraph>}
    </section>
  )
}

export function ExtractionQualityPanel({ runId }: { runId: string }) {
  const review = useQuery({
    queryKey: ['extraction-review', runId, 'quality'],
    queryFn: () => fetchExtractionReview(runId, { pageSize: 1 }),
  })
  if (review.isLoading) return <Skeleton active paragraph={{ rows: 2 }} />
  if (review.isError) return <Alert type="error" title={review.error.message} />
  return review.data ? <ExtractionQualitySummary quality={review.data.quality} /> : null
}
