import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Checkbox,
  Drawer,
  Empty,
  Input,
  List,
  Modal,
  Pagination,
  Popconfirm,
  Radio,
  Skeleton,
  Space,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import { useState } from 'react'

import {
  createMissedQuestion,
  deleteAnswer,
  fetchExtractionReview,
  fetchQuestionHistory,
  reviewExtractionItem,
  reviewExtractionItems,
  restoreQuestionHistory,
  updateQuestion,
  upsertAnswer,
  type ExtractionReviewItem,
  type QuestionHistoryEntry,
  type ReviewStatus,
  type SuspectedMiss,
  type TextChunk,
} from '../api/client'
import { ExtractionReviewCard } from './ExtractionReviewCard'

type Selection =
  | { kind: 'item'; value: ExtractionReviewItem }
  | { kind: 'miss'; value: SuspectedMiss }
  | null

interface Props {
  open: boolean
  runId: string | null
  chunks: TextChunk[]
  onClose: () => void
  onQuestionsChanged: () => void
  notify: (kind: 'success' | 'error', text: string) => void
}

const locatorLabel = (locator: Record<string, unknown>, ordinal: number) => {
  if (typeof locator.page === 'number') return `第 ${locator.page} 页`
  if (typeof locator.paragraph === 'number') return `第 ${locator.paragraph} 段`
  if (typeof locator.table === 'number') return `第 ${locator.table} 个表格`
  return `原文块 ${ordinal + 1}`
}

function highlightedContent(chunk: TextChunk, selection: Selection) {
  const ranges: Array<{ start: number; end: number; kind: 'question' | 'answer' | 'miss' }> = []
  if (selection?.kind === 'item') {
    const item = selection.value
    if (item.sourceChunkId === chunk.id) {
      ranges.push({ start: item.questionStart, end: item.questionEnd, kind: 'question' })
    }
    item.answerSpans
      .filter((span) => span.sourceChunkId === chunk.id)
      .forEach((span) => ranges.push({ start: span.start, end: span.end, kind: 'answer' }))
  } else if (selection?.kind === 'miss' && selection.value.sourceChunkId === chunk.id) {
    ranges.push({ start: selection.value.start, end: selection.value.end, kind: 'miss' })
  }
  if (ranges.length === 0) return chunk.content
  ranges.sort((left, right) => left.start - right.start)
  const parts: React.ReactNode[] = []
  let cursor = 0
  ranges.forEach((range, index) => {
    const start = Math.max(cursor, Math.min(range.start, chunk.content.length))
    const end = Math.max(start, Math.min(range.end, chunk.content.length))
    if (start > cursor) parts.push(chunk.content.slice(cursor, start))
    if (end > start) {
      parts.push(<mark className={`source-highlight-${range.kind}`} key={`${range.kind}-${index}`}>{chunk.content.slice(start, end)}</mark>)
    }
    cursor = end
  })
  if (cursor < chunk.content.length) parts.push(chunk.content.slice(cursor))
  return parts
}

export function ExtractionReviewDrawer({
  open,
  runId,
  chunks,
  onClose,
  onQuestionsChanged,
  notify,
}: Props) {
  const queryClient = useQueryClient()
  const [filter, setFilter] = useState<'ANY' | ReviewStatus>('ANY')
  const [page, setPage] = useState(1)
  const [selection, setSelection] = useState<Selection>(null)
  const [editing, setEditing] = useState<{
    item: ExtractionReviewItem
    kind: 'question' | 'answer'
  } | null>(null)
  const [draft, setDraft] = useState('')
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [historyTarget, setHistoryTarget] = useState<ExtractionReviewItem | null>(null)

  const review = useQuery({
    queryKey: ['extraction-review', runId, filter, page],
    queryFn: () =>
      fetchExtractionReview(runId!, {
        reviewStatus: filter === 'ANY' ? undefined : filter,
        page,
      }),
    enabled: open && Boolean(runId),
  })

  const history = useQuery({
    queryKey: ['question-history', historyTarget?.questionId],
    queryFn: () => fetchQuestionHistory(historyTarget!.questionId),
    enabled: Boolean(historyTarget),
  })

  const activeSelection: Selection =
    selection ?? (review.data?.items[0] ? { kind: 'item', value: review.data.items[0] } : null)

  const refresh = async () => {
    await queryClient.invalidateQueries({ queryKey: ['extraction-review', runId] })
    onQuestionsChanged()
  }

  const reviewMutation = useMutation({
    mutationFn: ({
      item,
      action,
    }: {
      item: ExtractionReviewItem
      action: 'CONFIRM' | 'REJECT' | 'RESET'
    }) =>
      reviewExtractionItem(item.occurrenceId, {
        action,
        expectedStatus: item.reviewStatus,
      }),
    onSuccess: async (updated) => {
      setSelection({ kind: 'item', value: updated })
      await refresh()
      notify('success', updated.reviewStatus === 'CONFIRMED' ? '已确认' : updated.reviewStatus === 'REJECTED' ? '已拒绝并从题库隐藏' : '已恢复为待复核')
    },
    onError: (error) => notify('error', error.message),
  })

  const selectedItems = review.data?.items.filter((item) => selectedIds.includes(item.occurrenceId)) ?? []
  const selectedArePending = selectedItems.length > 0 && selectedItems.every((item) => item.reviewStatus === 'PENDING')
  const selectedAreReviewed = selectedItems.length > 0 && selectedItems.every((item) => item.reviewStatus !== 'PENDING')
  const pageIds = review.data?.items.map((item) => item.occurrenceId) ?? []
  const allPageSelected = pageIds.length > 0 && pageIds.every((id) => selectedIds.includes(id))
  const somePageSelected = pageIds.some((id) => selectedIds.includes(id))

  const bulkReviewMutation = useMutation({
    mutationFn: (action: 'CONFIRM' | 'REJECT' | 'RESET') =>
      reviewExtractionItems(runId!, {
        items: selectedItems.map((item) => ({
          occurrenceId: item.occurrenceId,
          action,
          expectedStatus: item.reviewStatus,
        })),
      }),
    onSuccess: async (result) => {
      setSelectedIds([])
      setSelection(result.items[0] ? { kind: 'item', value: result.items[0] } : null)
      await refresh()
      notify('success', `已批量更新 ${result.updatedCount} 条复核结果`)
    },
    onError: (error) => notify('error', error.message),
  })

  const missedMutation = useMutation({
    mutationFn: (miss: SuspectedMiss) =>
      createMissedQuestion(runId!, {
        sourceChunkId: miss.sourceChunkId,
        start: miss.start,
        end: miss.end,
        quote: miss.quote,
        prompt: miss.quote.replace(/^\s{0,3}#{1,6}\s*/, '').trim(),
      }),
    onSuccess: async (created) => {
      setSelection({ kind: 'item', value: created })
      await refresh()
      notify('success', '漏题已补建并标记为已确认')
    },
    onError: (error) => notify('error', error.message),
  })

  const editMutation = useMutation({
    mutationFn: async () => {
      if (!editing) return
      if (editing.kind === 'question') {
        await updateQuestion(editing.item.questionId, {
          prompt: draft.trim(),
          expectedVersion: editing.item.questionVersion,
        })
      } else {
        await upsertAnswer(editing.item.questionId, {
          content: draft,
          expectedVersion: editing.item.answer?.version ?? 0,
        })
      }
    },
    onSuccess: async () => {
      setEditing(null)
      await refresh()
      notify('success', '修改已保存，请继续确认题答配对')
    },
    onError: (error) => notify('error', error.message),
  })

  const deleteAnswerMutation = useMutation({
    mutationFn: (item: ExtractionReviewItem) =>
      deleteAnswer(item.questionId, item.answer!.version),
    onSuccess: async () => {
      await refresh()
      notify('success', '错误答案已删除，题目已转为无答案状态')
    },
    onError: (error) => notify('error', error.message),
  })

  const restoreMutation = useMutation({
    mutationFn: ({ entry, item }: { entry: QuestionHistoryEntry; item: ExtractionReviewItem }) =>
      restoreQuestionHistory(item.questionId, entry.id, {
        expectedVersion:
          entry.field === 'QUESTION'
            ? item.questionVersion
            : entry.field === 'ANSWER'
              ? item.answer?.version ?? 0
              : 0,
        expectedReviewStatus: entry.field === 'REVIEW_STATUS' ? item.reviewStatus : undefined,
      }),
    onSuccess: async () => {
      await refresh()
      await queryClient.invalidateQueries({ queryKey: ['question-history', historyTarget?.questionId] })
      notify('success', '已恢复到该次变更前的内容')
    },
    onError: (error) => notify('error', error.message),
  })

  const locate = (next: Selection) => {
    setSelection(next)
    const chunkId = next?.kind === 'item' ? next.value.sourceChunkId : next?.value.sourceChunkId
    if (chunkId) {
      requestAnimationFrame(() => document.getElementById(`review-source-${chunkId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }))
    }
  }

  return (
    <Drawer
      classNames={{ root: 'review-drawer' }}
      title="题答提取质量复核"
      size="96vw"
      open={open}
      onClose={onClose}
      destroyOnHidden
    >
      <Alert
        type="info"
        showIcon
        title="左侧核对原文，右侧确认题答"
        description="黄色项目是系统根据问号、标题和常见问句模式发现的疑似漏题，只是辅助提示，可能包含普通章节标题。"
      />
      <div className="review-layout">
        <section className="review-source-pane" aria-label="来源原文">
          <Typography.Title level={4}>来源原文</Typography.Title>
          {chunks.map((chunk) => (
            <article
              id={`review-source-${chunk.id}`}
              className={`review-source-block ${activeSelection && ((activeSelection.kind === 'item' && activeSelection.value.sourceChunkId === chunk.id) || (activeSelection.kind === 'miss' && activeSelection.value.sourceChunkId === chunk.id)) ? 'selected' : ''}`}
              key={chunk.id}
            >
              <Typography.Text type="secondary">{locatorLabel(chunk.locator, chunk.ordinal)}</Typography.Text>
              <pre>{highlightedContent(chunk, activeSelection)}</pre>
            </article>
          ))}
        </section>

        <section className="review-result-pane" aria-label="提取结果">
          {review.isLoading && <Skeleton active />}
          {review.isError && <Alert type="error" title={review.error.message} />}
          {review.data && (
            <>
              <div className="review-summary">
                <Space wrap>
                  <Tag color="gold">待复核 {review.data.pendingCount}</Tag>
                  <Tag color="green">已确认 {review.data.confirmedCount}</Tag>
                  <Tag color="red">已拒绝 {review.data.rejectedCount}</Tag>
                  <Tag color="orange">疑似漏题 {review.data.suspectedMisses.length}</Tag>
                </Space>
              </div>
              <Tabs
                items={[
                  {
                    key: 'results',
                    label: '提取结果',
                    children: (
                      <>
                        <Radio.Group
                          value={filter}
                          onChange={(event) => {
                            setFilter(event.target.value)
                            setPage(1)
                            setSelection(null)
                            setSelectedIds([])
                          }}
                          optionType="button"
                          buttonStyle="solid"
                          options={[
                            { label: '全部', value: 'ANY' },
                            { label: '待复核', value: 'PENDING' },
                            { label: '已确认', value: 'CONFIRMED' },
                            { label: '已拒绝', value: 'REJECTED' },
                          ]}
                        />
                        <div className="review-bulk-actions">
                          <Space wrap>
                            <Checkbox
                              checked={allPageSelected}
                              indeterminate={!allPageSelected && somePageSelected}
                              onChange={(event) => setSelectedIds(event.target.checked ? pageIds : [])}
                            >
                              选择本页
                            </Checkbox>
                            <Typography.Text type="secondary">已选 {selectedItems.length} 条</Typography.Text>
                            <Button
                              size="small"
                              type="primary"
                              disabled={!selectedArePending}
                              loading={bulkReviewMutation.isPending}
                              onClick={() => bulkReviewMutation.mutate('CONFIRM')}
                            >
                              批量确认
                            </Button>
                            <Popconfirm
                              title={`拒绝选中的 ${selectedItems.length} 条结果？`}
                              description="拒绝后会从普通题库隐藏，复核记录仍会保留。"
                              disabled={!selectedArePending}
                              onConfirm={() => bulkReviewMutation.mutate('REJECT')}
                            >
                              <Button
                                size="small"
                                danger
                                disabled={!selectedArePending}
                                loading={bulkReviewMutation.isPending}
                              >
                                批量拒绝
                              </Button>
                            </Popconfirm>
                            <Button
                              size="small"
                              disabled={!selectedAreReviewed}
                              loading={bulkReviewMutation.isPending}
                              onClick={() => bulkReviewMutation.mutate('RESET')}
                            >
                              批量重新复核
                            </Button>
                          </Space>
                        </div>
                        <div className="review-list">
                          {review.data.items.map((item) => (
                            <ExtractionReviewCard
                              key={item.occurrenceId}
                              item={item}
                              busy={reviewMutation.isPending && reviewMutation.variables?.item.occurrenceId === item.occurrenceId}
                              selected={selectedIds.includes(item.occurrenceId)}
                              onSelect={(target, selected) => setSelectedIds((current) => {
                                if (!selected) {
                                  return current.filter((id) => id !== target.occurrenceId)
                                }
                                return current.includes(target.occurrenceId)
                                  ? current
                                  : [...current, target.occurrenceId]
                              })}
                              onLocate={(target) => locate({ kind: 'item', value: target })}
                              onEdit={(target, kind) => {
                                setEditing({ item: target, kind })
                                setDraft(
                                  kind === 'question'
                                    ? target.prompt
                                    : target.answer?.content ?? target.sourceAnswer ?? '',
                                )
                              }}
                              onDeleteAnswer={(target) => deleteAnswerMutation.mutate(target)}
                              onHistory={setHistoryTarget}
                              onReview={(target, action) => reviewMutation.mutate({ item: target, action })}
                            />
                          ))}
                          {review.data.items.length === 0 && <Empty description="当前筛选下没有复核项" />}
                        </div>
                        {review.data.total > review.data.pageSize && (
                          <Pagination current={page} pageSize={review.data.pageSize} total={review.data.total} showSizeChanger={false} onChange={(nextPage) => { setPage(nextPage); setSelectedIds([]) }} />
                        )}
                      </>
                    ),
                  },
                  {
                    key: 'misses',
                    label: `疑似漏题 ${review.data.suspectedMisses.length}`,
                    children: review.data.suspectedMisses.length > 0 ? (
                      <div className="review-list">
                        {review.data.suspectedMisses.map((miss) => (
                          <article className="review-card suspected-miss-card" key={`${miss.sourceChunkId}-${miss.start}`}>
                            <Space wrap>
                              <Tag color="orange">疑似漏题</Tag>
                              <Typography.Text type="secondary">{locatorLabel(miss.sourceLocator, miss.sourceBlockOrdinal)}</Typography.Text>
                            </Space>
                            <blockquote>{miss.quote}</blockquote>
                            <Space wrap>
                              <Button size="small" onClick={() => locate({ kind: 'miss', value: miss })}>定位原文</Button>
                              <Button type="primary" size="small" loading={missedMutation.isPending} onClick={() => missedMutation.mutate(miss)}>补建为题目</Button>
                            </Space>
                          </article>
                        ))}
                      </div>
                    ) : <Empty description="没有发现明显的疑似漏题" />,
                  },
                ]}
              />
            </>
          )}
        </section>
      </div>

      <Modal
        title={editing?.kind === 'question' ? '修改问题' : '修改答案'}
        open={Boolean(editing)}
        okText="保存"
        cancelText="取消"
        confirmLoading={editMutation.isPending}
        okButtonProps={{ disabled: !draft.trim() }}
        onOk={() => editMutation.mutate()}
        onCancel={() => setEditing(null)}
      >
        <Input.TextArea rows={12} value={draft} onChange={(event) => setDraft(event.target.value)} />
      </Modal>
      <Modal
        title="修改历史"
        open={Boolean(historyTarget)}
        footer={null}
        width={760}
        onCancel={() => setHistoryTarget(null)}
      >
        {history.isLoading && <Skeleton active />}
        {history.isError && <Alert type="error" title={history.error.message} />}
        {history.data && (
          <List
            locale={{ emptyText: '这道题还没有修改记录' }}
            dataSource={history.data.items}
            renderItem={(entry) => {
              const currentItem = review.data?.items.find(
                (item) => item.questionId === historyTarget?.questionId,
              ) ?? historyTarget
              const fieldLabel = {
                QUESTION: '问题',
                ANSWER: '答案',
                REVIEW_STATUS: '复核状态',
              }[entry.field]
              const actionLabel = {
                UPDATED: '已修改',
                DELETED: '已删除',
                RESTORED: '已恢复',
              }[entry.action]
              return (
                <List.Item
                  actions={currentItem ? [
                    <Popconfirm
                      key="restore"
                      title="恢复到这次变更前？"
                      description="当前内容会作为新的历史记录保留。"
                      onConfirm={() => restoreMutation.mutate({ entry, item: currentItem })}
                    >
                      <Button size="small" loading={restoreMutation.isPending}>恢复</Button>
                    </Popconfirm>,
                  ] : undefined}
                >
                  <List.Item.Meta
                    title={`${fieldLabel} · ${actionLabel} · ${new Date(entry.createdAt).toLocaleString()}`}
                    description={(
                      <div className="question-history-values">
                        <Typography.Text type="secondary">变更前</Typography.Text>
                        <pre>{entry.previousValue ?? '（空）'}</pre>
                        <Typography.Text type="secondary">变更后</Typography.Text>
                        <pre>{entry.newValue ?? '（空）'}</pre>
                      </div>
                    )}
                  />
                </List.Item>
              )
            }}
          />
        )}
      </Modal>
    </Drawer>
  )
}
