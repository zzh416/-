import { useQuery } from '@tanstack/react-query'
import { Alert, Button, Card, Drawer, Empty, Input, Pagination, Select, Skeleton, Space, Tag, Typography } from 'antd'
import { useState } from 'react'
import { fetchLearning, type LearningFilter } from '../api/learning'
import { LearningSelect } from './LearningState'
import { SafeMarkdown } from './SafeMarkdown'

export function LearningDrawer({ open, onClose, onOpenInterview }: { open: boolean; onClose: () => void; onOpenInterview: (id: string) => void }) {
  const [filter, setFilter] = useState<LearningFilter>('TO_REVIEW')
  const [search, setSearch] = useState('')
  const [page, setPage] = useState(1)
  const query = useQuery({ queryKey: ['learning', 'items', filter, search, page], queryFn: () => fetchLearning(filter, search, page), enabled: open })
  const data = query.data
  return <Drawer title="薄弱题复习" size={800} open={open} onClose={onClose} styles={{ wrapper: { maxWidth: '100vw' } }}>
    <Space orientation="vertical" size="middle" className="profile-stack">
      <Alert type="info" showIcon title="按当前技能和偏好复习薄弱题" description="不会优先，其次不熟悉，再是内容更新后需重新确认的题。标记已掌握后移出默认待复习列表；自评不会改变原复习清单。面试不会题需在面试记录中逐条确认。" />
      {data && <Space wrap><Tag color="red">不会 {data.unknownCount}</Tag><Tag color="orange">不熟悉 {data.weakCount}</Tag><Tag color="green">已掌握 {data.masteredCount}</Tag><Tag>内容更新 {data.changedCount}</Tag></Space>}
      {Boolean(data?.excludedCount) && <Typography.Text type="secondary">有 {data!.excludedCount} 条已标记记录按当前筛选规则或原题可见性隐藏；原标记保留。</Typography.Text>}
      <Select aria-label="按掌握程度筛选" style={{ width: '100%' }} value={filter} onChange={(value) => { setFilter(value); setPage(1) }} options={[
        { value: 'TO_REVIEW', label: '待复习：不会、不熟悉、内容更新' }, { value: 'ANY', label: '全部已标记' },
        { value: 'UNKNOWN', label: '不会' }, { value: 'WEAK', label: '不熟悉' }, { value: 'MASTERED', label: '已掌握' }]} />
      <Input.Search aria-label="搜索薄弱题" placeholder="搜索题目或可用答案" allowClear onSearch={(value) => { setSearch(value); setPage(1) }} />
      {query.isLoading && <Skeleton active />}
      {query.isError && <Alert type="error" showIcon title="薄弱题加载失败" description={query.error.message} action={<Button onClick={() => void query.refetch()}>重试</Button>} />}
      {data && <Typography.Text type="secondary">当前筛选共 {data.total} 题</Typography.Text>}
      {data?.items.length === 0 && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={page > 1 ? '本页暂无题目，可返回首页。' : '暂无符合条件的题目。在题库或复习清单中标记不会／不熟悉，或确认面试不会题后再来复习。'}>
        {page > 1 && <Button onClick={() => setPage(1)}>返回首页</Button>}
      </Empty>}
      {data?.items.map((item) => <Card key={item.id} size="small">
        <Space orientation="vertical" size="middle" className="profile-stack">
          <Tag color={item.sourceKind === 'BANK' ? 'blue' : 'purple'}>{item.sourceKind === 'BANK' ? '题库已有题' : '面试生成题 · 独立保存'}</Tag>
          <SafeMarkdown className="question-text">{item.prompt}</SafeMarkdown>
          {item.answerContent ? <div className="recommendation-answer"><Typography.Text strong>当前适用资料答案</Typography.Text><SafeMarkdown className="answer-text">{item.answerContent}</SafeMarkdown></div>
            : <Typography.Text type="secondary">当前没有适用的资料答案；模型反馈不会作为参考答案保存。</Typography.Text>}
          <LearningSelect item={item} />
          {item.updatedAt && <Typography.Text type="secondary">最近自评：{new Date(item.updatedAt).toLocaleString()}</Typography.Text>}
          {item.sourceSessionId && <Button onClick={() => onOpenInterview(item.sourceSessionId!)}>回看来源面试与反馈</Button>}
        </Space>
      </Card>)}
      {data && data.total > 20 && <Pagination current={page} pageSize={20} total={data.total} showSizeChanger={false} onChange={setPage} />}
    </Space>
  </Drawer>
}
