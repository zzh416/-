import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Drawer, Empty, Input, Pagination, Radio, Select, Skeleton, Space, Typography } from 'antd'
import { useState } from 'react'

import {
  createRecommendationRun,
  fetchLatestRecommendationRun,
  fetchRecommendationItems,
  type RecommendationDecision,
  type UsableAnswerState,
} from '../api/client'
import { RecommendationItemCard } from './RecommendationItemCard'
import { RecommendationExportButton } from './RecommendationExportButton'
import { ProjectFollowupsButton } from './ProjectFollowupsButton'


interface RecommendationDrawerProps {
  open: boolean
  onClose: () => void
  notify: (kind: 'success' | 'error', text: string) => void
}

export function RecommendationDrawer({ open, onClose, notify }: RecommendationDrawerProps) {
  const queryClient = useQueryClient()
  const [decision, setDecision] = useState<'ANY' | RecommendationDecision>('INCLUDED')
  const [answerState, setAnswerState] = useState<'ANY' | UsableAnswerState>('ANY')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const [projectFilter, setProjectFilter] = useState('ANY')

  const latest = useQuery({
    queryKey: ['recommendation-run', 'latest'],
    queryFn: fetchLatestRecommendationRun,
    enabled: open,
  })
  const items = useQuery({
    queryKey: ['recommendation-items', latest.data?.id, decision, answerState, search, page, projectFilter],
    queryFn: () =>
      fetchRecommendationItems(latest.data!.id, {
        decision,
        usableAnswerState: answerState,
        q: search,
        page,
        pageSize: 20,
        projectId: !['ANY', 'UNMATCHED'].includes(projectFilter) ? projectFilter : undefined,
        unmatchedOnly: projectFilter === 'UNMATCHED',
      }),
    enabled: open && Boolean(latest.data?.id),
  })
  const generate = useMutation({
    mutationFn: createRecommendationRun,
    onSuccess: async () => {
      setPage(1)
      setProjectFilter('ANY')
      await queryClient.invalidateQueries({ queryKey: ['recommendation-run'] })
      await queryClient.invalidateQueries({ queryKey: ['recommendation-items'] })
      notify('success', '个性化复习清单已生成')
    },
    onError: (error: Error) => notify('error', error.message),
  })

  const run = latest.data
  const hasNoProfile = run?.profileVersion === 0

  return (
    <Drawer
      classNames={{ root: 'recommendation-drawer' }}
      title="我的复习清单"
      size={780}
      open={open}
      onClose={onClose}
      extra={
        <Button type="primary" loading={generate.isPending} onClick={() => generate.mutate()}>
          {run ? '重新生成' : '生成清单'}
        </Button>
      }
    >
      {latest.isLoading && <Skeleton active />}
      {latest.isError && <Alert type="error" showIcon title={latest.error.message} />}
      {!latest.isLoading && !latest.isError && !run && (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有个性化清单。系统会根据已确认技能和启用中的偏好生成。"
        >
          <Button type="primary" loading={generate.isPending} onClick={() => generate.mutate()}>
            生成第一份清单
          </Button>
        </Empty>
      )}

      {run && (
        <>
          {!run.isCurrent && (
            <Alert
              className="recommendation-alert"
              type="warning"
              showIcon
              title="画像、偏好、题库或关联规则已经变化"
              description="当前展示的是历史结果，请重新生成后再按最新条件复习。"
            />
          )}
          {hasNoProfile && (
            <Alert
              className="recommendation-alert"
              type="info"
              showIcon
              title="这份清单没有使用简历画像"
              description="可以先在“我的画像”中确认技能，再重新生成。已配置的偏好仍然有效。"
            />
          )}

          <div className="recommendation-summary" aria-label="清单统计">
            <div><strong>{run.includedCount}</strong><span>进入清单</span></div>
            <div><strong>{run.excludedCount}</strong><span>已排除</span></div>
            <div><strong>{run.answeredCount}</strong><span>有可用答案</span></div>
            <div><strong>{run.missingCount}</strong><span>当前无答案</span></div>
          </div>

          <Typography.Paragraph type="secondary" className="recommendation-version">
            画像版本 {run.profileVersion} · 规则 {run.ruleSnapshot.length} 条 · 算法 {run.algorithmVersion}
            {' · '}{new Date(run.createdAt).toLocaleString()}
          </Typography.Paragraph>

          <Typography.Paragraph>
            <Space wrap>
              <RecommendationExportButton key={run.id} run={run} notify={notify} />
              <ProjectFollowupsButton key={`followups-${run.id}`} run={run} initialProjectId={projectFilter} notify={notify} />
            </Space>
          </Typography.Paragraph>

          {run.profileSnapshot.projects.length === 0 && (
            <Typography.Paragraph type="secondary">
              这份清单没有项目记录，可在“我的画像 → 我的项目”中补充并确认，再重新生成。现有通用题仍然保留。
            </Typography.Paragraph>
          )}

          <div className="recommendation-filters">
            <Select
              aria-label="按关联项目筛选"
              value={projectFilter}
              style={{ width: '100%' }}
              onChange={(value) => { setPage(1); setProjectFilter(value) }}
              options={[
                { value: 'ANY', label: '全部项目与通用题' },
                { value: 'UNMATCHED', label: '未匹配项目／通用复习' },
                ...run.profileSnapshot.projects.filter((project) => project.reviewStatus === 'CONFIRMED').map((project) => ({ value: project.id, label: project.name })),
              ]}
              disabled={!run.profileSnapshot.projectMatchingVersion}
            />
            <Radio.Group
              value={decision}
              optionType="button"
              buttonStyle="solid"
              onChange={(event) => {
                setPage(1)
                setDecision(event.target.value)
              }}
              options={[
                { label: `进入清单 ${run.includedCount}`, value: 'INCLUDED' },
                { label: `已排除 ${run.excludedCount}`, value: 'EXCLUDED' },
                { label: `全部 ${run.totalCount}`, value: 'ANY' },
              ]}
            />
            <Radio.Group
              value={answerState}
              optionType="button"
              onChange={(event) => {
                setPage(1)
                setAnswerState(event.target.value)
              }}
              options={[
                { label: '全部答案', value: 'ANY' },
                { label: '有可用答案', value: 'ANSWERED' },
                { label: '当前无答案', value: 'MISSING' },
              ]}
            />
            <Input.Search
              allowClear
              aria-label="搜索个性化清单"
              placeholder="搜索题目或答案"
              onSearch={(value) => {
                setPage(1)
                setSearch(value.trim())
              }}
            />
          </div>

          {items.data && <Typography.Paragraph type="secondary">当前筛选共 {items.data.total} 题</Typography.Paragraph>}
          {items.isLoading && <Skeleton active />}
          {items.isError && <Alert type="error" showIcon title={items.error.message} />}
          {items.data?.items.length === 0 && (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="当前筛选条件下没有题目" />
          )}
          <Space orientation="vertical" size="middle" className="recommendation-list">
            {items.data?.items.map((item) => (
              <RecommendationItemCard key={item.id} item={item} />
            ))}
          </Space>
          {(items.data?.total ?? 0) > 20 && (
            <Pagination
              current={page}
              pageSize={20}
              total={items.data?.total ?? 0}
              showSizeChanger={false}
              onChange={setPage}
            />
          )}
        </>
      )}
    </Drawer>
  )
}
