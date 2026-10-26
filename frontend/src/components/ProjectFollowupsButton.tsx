import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Drawer, Empty, Form, Input, Select, Skeleton, Space, Tag, Typography } from 'antd'
import { useState } from 'react'

import { createProjectFollowups, fetchProjectFollowup, fetchProjectFollowupExport, fetchProjectFollowupHistory, fetchRecommendationItems, retryProjectFollowup, type FollowupStatus, type RecommendationRun } from '../api/client'
import { ProjectFollowupResults } from './ProjectFollowupResults'

const statusLabels: Record<FollowupStatus, string> = { QUEUED: '等待生成', PROCESSING: '正在生成', SUCCEEDED: '生成完成', PARTIAL_SUCCESS: '部分完成', WAITING_FOR_CONFIGURATION: '等待模型配置', FAILED: '生成失败' }
const running = (status?: FollowupStatus) => status === 'QUEUED' || status === 'PROCESSING'

interface Props {
  run: RecommendationRun
  initialProjectId?: string
  notify: (kind: 'success' | 'error', text: string) => void
}

export function ProjectFollowupsButton({ run, initialProjectId, notify }: Props) {
  const queryClient = useQueryClient()
  const projects = run.profileSnapshot.projects.filter((project) => project.reviewStatus === 'CONFIRMED')
  const [open, setOpen] = useState(false)
  const [projectId, setProjectId] = useState('')
  const [seeds, setSeeds] = useState<Array<{ value: string; label: string }>>([])
  const [search, setSearch] = useState('')
  const [activeId, setActiveId] = useState<string | null>(null)
  const choices = useQuery({
    queryKey: ['followup-seeds', run.id, projectId, search],
    queryFn: () => fetchRecommendationItems(run.id, { decision: 'INCLUDED', projectId, q: search, pageSize: 100 }),
    enabled: open && Boolean(projectId),
  })
  const history = useQuery({ queryKey: ['followup-history', projectId], queryFn: () => fetchProjectFollowupHistory(projectId), enabled: open && Boolean(projectId) })
  const taskId = activeId ?? history.data?.items[0]?.id
  const task = useQuery({
    queryKey: ['project-followup', taskId], queryFn: () => fetchProjectFollowup(taskId!), enabled: open && Boolean(taskId),
    refetchInterval: (query) => running(query.state.data?.status) ? 1500 : false,
  })
  const onError = (error: Error) => {
    notify('error', error.message)
    void queryClient.invalidateQueries({ queryKey: ['followup-history'] })
  }
  const generate = useMutation({
    mutationFn: () => createProjectFollowups(run.id, { projectId, seedItemIds: seeds.map((seed) => seed.value) }),
    onSuccess: (next) => {
      queryClient.setQueryData(['project-followup', next.id], next)
      setActiveId(next.id)
      void queryClient.invalidateQueries({ queryKey: ['followup-history'] })
      notify('success', '已提交项目追问生成')
    }, onError,
  })
  const retry = useMutation({
    mutationFn: () => retryProjectFollowup(taskId!),
    onSuccess: (next) => queryClient.setQueryData(['project-followup', next.id], next), onError,
  })
  const download = useMutation({
    mutationFn: () => fetchProjectFollowupExport(taskId!),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = `项目追问链-${taskId!.slice(0, 8)}.md`
      document.body.append(anchor); anchor.click(); anchor.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
      notify('success', '追问链已开始下载')
    }, onError,
  })

  return <>
    <Button disabled={projects.length === 0} title={projects.length ? undefined : '请先确认项目并重新生成清单'} onClick={() => {
      setProjectId(projects.some((project) => project.id === initialProjectId) ? initialProjectId! : projects[0].id)
      setSeeds([]); setSearch(''); setActiveId(null); setOpen(true)
    }}>项目追问链</Button>
    <Drawer title="项目追问链 · AI 推演" size={760} open={open} onClose={() => setOpen(false)}>
      <Space orientation="vertical" size="middle" className="profile-stack">
        <Alert type="info" showIcon title="从已有题目继续追问，每题最多3层"
          description="点击生成会将所选项目名称、说明、种子题及筛选约束发送给已配置的文本模型，可能产生 API 费用。不发送完整简历、答案或个人备注；不会生成答案或改写原题库。" />
        {!run.isCurrent && <Alert type="warning" showIcon title="请先重新生成复习清单，再生成新追问；历史结果仍可查看。" />}
        <Form layout="vertical">
          <Form.Item label="选择项目">
            <Select aria-label="追问项目" value={projectId || undefined} disabled={generate.isPending} options={projects.map((project) => ({ value: project.id, label: project.name }))}
              onChange={(value) => { setProjectId(value); setSeeds([]); setSearch(''); setActiveId(null) }} />
          </Form.Item>
          <Form.Item label="查找该项目已关联的题目">
            <Input.Search aria-label="搜索追问种子题" allowClear placeholder="题干或答案关键词" onSearch={setSearch} />
          </Form.Item>
          <Form.Item label="选择种子题（最多3道）" extra={choices.data && choices.data.total > 100 ? '当前显示前100题，请通过关键词缩小范围后选择。' : '种子题来自该项目全部进入清单的题目。'}>
            <Select<Array<{ value: string; label: string }>> aria-label="追问种子题" mode="multiple" labelInValue maxCount={3} value={seeds}
              optionFilterProp="label"
              loading={choices.isLoading} disabled={generate.isPending} onChange={setSeeds} placeholder="选择已有题目"
              options={choices.data?.items.map((item) => ({ value: item.id, label: item.prompt.slice(0, 100) }))} />
          </Form.Item>
          {choices.isError && <Alert type="error" title={choices.error.message} />}
          <Button type="primary" loading={generate.isPending} disabled={!run.isCurrent || !projectId || !seeds.length || running(task.data?.status)} onClick={() => generate.mutate()}>生成项目追问</Button>
        </Form>
        {history.isError && <Alert type="error" showIcon title={history.error.message} />}
        {Boolean(history.data?.items.length) && <Form.Item label="最近10次生成记录（含历史清单）">
          <Select aria-label="追问生成记录" value={taskId} onChange={setActiveId} options={history.data?.items.map((item) => ({ value: item.id, label: `${new Date(item.createdAt).toLocaleString()} · ${statusLabels[item.id === taskId && task.data ? task.data.status : item.status]}` }))} />
        </Form.Item>}
        {task.isLoading && <Skeleton active />}
        {task.isError && <Alert type="error" showIcon title={task.error.message} />}
        {task.data && <>
          <Space wrap>
            <Tag color={running(task.data.status) ? 'processing' : 'default'}>{statusLabels[task.data.status]}</Tag>
            {task.data.canRetry && <Button loading={retry.isPending} onClick={() => retry.mutate()}>重试追问（{task.data.retryCount}/3）</Button>}
            {task.data.items.length > 0 && <Button loading={download.isPending} onClick={() => download.mutate()}>导出追问 Markdown</Button>}
          </Space>
          {running(task.data.status) && <Typography.Text type="secondary">正在后台生成，可关闭面板，稍后重新打开查看。等待超过10分钟后会提供重试入口。</Typography.Text>}
          <ProjectFollowupResults run={task.data} />
        </>}
        {!history.isLoading && !taskId && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择项目和已有题目后，生成第一条追问链。" />}
      </Space>
    </Drawer>
  </>
}
