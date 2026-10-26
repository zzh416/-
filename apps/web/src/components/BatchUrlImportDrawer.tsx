import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Divider, Drawer, Empty, Flex, Form, Input, Progress, Select, Skeleton, Space, Tag, Typography } from 'antd'
import { useEffect, useId, useState } from 'react'

import { retryAsset, type AssetStatus, type AssetSummary } from '../api/client'
import {
  createUrlImport,
  dispatchUrlImport,
  fetchUrlImport,
  fetchUrlImports,
  type UrlImportBatch,
  type UrlImportDisposition,
  type UrlImportItem,
  type UrlImportPage,
} from '../api/urlImports'

interface BatchUrlImportDrawerProps {
  open: boolean
  collectionId: string
  onClose: () => void
  onOpenAsset: (id: string) => void
}

const statusLabels: Record<AssetStatus, string> = {
  QUEUED: '等待解析',
  PROCESSING: '正在解析',
  READY: '解析完成',
  PARTIAL_SUCCESS: '部分解析完成',
  WAITING_FOR_CONFIGURATION: '等待配置',
  FAILED: '解析失败',
}

const statusColors: Record<AssetStatus, string> = {
  QUEUED: 'default',
  PROCESSING: 'processing',
  READY: 'success',
  PARTIAL_SUCCESS: 'warning',
  WAITING_FOR_CONFIGURATION: 'warning',
  FAILED: 'error',
}

const dispositionLabels: Record<UrlImportDisposition, string> = {
  CREATED: '新建资料',
  REUSED: '复用已有资料',
  DUPLICATE: '批内重复',
  INVALID: '无效链接',
}

const stageLabels: Record<string, string> = {
  STORED: '链接已保存',
  FETCHING: '正在抓取网页',
  RENDERING: '正在运行网页脚本',
  EXTRACTING: '正在提取文字',
  OCR: '正在识别图片',
  CONFIGURATION: '等待配置',
  COMPLETE: '解析完成',
  FAILED: '解析失败',
}

const isPending = (asset: AssetSummary) =>
  asset.status === 'QUEUED' || asset.status === 'PROCESSING'

const canRetry = (asset: AssetSummary) =>
  Boolean(asset.job && asset.job.retryCount < 3 &&
    ['FAILED', 'WAITING_FOR_CONFIGURATION', 'PARTIAL_SUCCESS'].includes(asset.status))

const distinctAssets = (batch: UrlImportBatch) =>
  [...new Map(batch.items.flatMap((item) => item.asset ? [[item.asset.id, item.asset] as const] : [])).values()]

function ImportSummary({ batch }: { batch: UrlImportBatch }) {
  const assets = distinctAssets(batch)
  const settled = assets.filter((asset) => !isPending(asset)).length
  const ready = assets.filter((asset) => asset.status === 'READY').length
  const counts = (disposition: UrlImportDisposition) =>
    batch.items.filter((item) => item.disposition === disposition).length

  return (
    <Flex vertical gap="small" aria-live="polite">
      <Space wrap>
        <Tag>总行数 {batch.items.length}</Tag>
        <Tag color="blue">新建 {counts('CREATED')}</Tag>
        <Tag>复用 {counts('REUSED')}</Tag>
        <Tag>批内重复 {counts('DUPLICATE')}</Tag>
        <Tag color={counts('INVALID') ? 'error' : 'default'}>无效 {counts('INVALID')}</Tag>
      </Space>
      {assets.length > 0 && <>
        <Typography.Text>
          实际资料 {assets.length} 份 · 已结束 {settled}/{assets.length} · 解析完成 {ready} · 处理中 {assets.length - settled}
        </Typography.Text>
        <Progress
          percent={Math.round(settled / assets.length * 100)}
          success={{ percent: Math.round(ready / assets.length * 100) }}
          status={settled === assets.length && ready < assets.length ? 'exception' : undefined}
          aria-label={`资料解析进度，${assets.length} 份中 ${settled} 份已结束，${ready} 份解析完成`}
        />
        <Typography.Text type="secondary">
          按不同资料统计进度；复用和批内重复共用原资料，不新增解析任务。已结束包含失败及等待配置，请查看逐条结果。
        </Typography.Text>
      </>}
    </Flex>
  )
}

function ImportItemCard({ item, retryingId, retryBusy, onRetry, onOpenAsset }: {
  item: UrlImportItem
  retryingId?: string
  retryBusy: boolean
  onRetry: (id: string) => void
  onOpenAsset: (id: string) => void
}) {
  const asset = item.asset
  const errorText = asset?.job?.errorMessage ?? asset?.job?.errorCode
  return (
    <Card size="small">
      <Flex vertical gap="small">
        <Space wrap>
          <Typography.Text strong>第 {item.ordinal} 条</Typography.Text>
          <Tag color={item.disposition === 'INVALID' ? 'error' : item.disposition === 'CREATED' ? 'blue' : 'default'}>
            {dispositionLabels[item.disposition]}
          </Tag>
          {asset && <Tag color={statusColors[asset.status]}>{statusLabels[asset.status]}</Tag>}
        </Space>
        <Typography.Paragraph style={{ overflowWrap: 'anywhere', whiteSpace: 'pre-wrap', marginBottom: 0 }}>
          {item.inputUrl}
        </Typography.Paragraph>
        {item.disposition === 'REUSED' && <Typography.Text type="secondary">此资料库已有相同网页，复用现有资料和解析结果。</Typography.Text>}
        {item.disposition === 'DUPLICATE' && <Typography.Text type="secondary">
          {item.duplicateOf === null ? '与本批其他链接重复' : `与第 ${item.duplicateOf} 条重复`}，共用资料，不增加任务。
        </Typography.Text>}
        {(item.message || item.disposition === 'INVALID') && <Typography.Text type={item.disposition === 'INVALID' ? 'danger' : 'secondary'}>
          {item.message ?? '链接无效，请检查网址是否完整后重新导入。'}
        </Typography.Text>}
        {!asset && item.disposition !== 'INVALID' && <Tag>资料已删除</Tag>}
        {asset && <>
          <Typography.Text strong style={{ overflowWrap: 'anywhere' }}>{asset.title}</Typography.Text>
          {asset.job && <Typography.Text type="secondary">
            {stageLabels[asset.job.stage] ?? asset.job.stage}
            {asset.job.totalUnits ? ` · ${asset.job.processedUnits}/${asset.job.totalUnits}` : ''}
          </Typography.Text>}
          {errorText && <Typography.Text type="danger" style={{ overflowWrap: 'anywhere' }}>{errorText}</Typography.Text>}
          {asset.status === 'FAILED' && !errorText && <Typography.Text type="danger">解析失败，未返回详细原因。请打开资料查看详情或重试解析。</Typography.Text>}
          {asset.job && asset.job.retryCount >= 3 && !isPending(asset) && asset.status !== 'READY' &&
            <Typography.Text type="secondary">已达到最多 3 次重试，请打开资料查看解析详情。</Typography.Text>}
          <Space wrap>
            <Button onClick={() => onOpenAsset(asset.id)}>打开资料</Button>
            {canRetry(asset) && <Button
              loading={retryBusy && retryingId === asset.id}
              disabled={retryBusy}
              onClick={() => onRetry(asset.id)}
            >重试解析（{asset.job!.retryCount}/3）</Button>}
          </Space>
        </>}
      </Flex>
    </Card>
  )
}

function BatchUrlImportContent({ open, collectionId, onOpenAsset }: Omit<BatchUrlImportDrawerProps, 'onClose'>) {
  const queryClient = useQueryClient()
  const inputId = useId()
  const [draft, setDraft] = useState(() => ({ text: '', idempotencyKey: crypto.randomUUID() }))
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const urls = draft.text.split(/\r\n?|\n/).filter((line) => line.trim().length > 0)
  const tooMany = urls.length > 50
  const tooLongCount = urls.filter((line) => line.trim().length > 2048).length
  const exceedsRequestLimit = urls.some((line) => line.length > 4096)
  const inputError = tooMany
    ? `当前有 ${urls.length} 行，请减少到 50 行以内。`
    : exceedsRequestLimit ? '存在超过请求上限的超长行，请将每个网址缩短到 2048 字符以内后再导入。' : undefined
  const historyKey = ['url-imports', collectionId]
  const history = useQuery({
    queryKey: historyKey,
    queryFn: () => fetchUrlImports(collectionId),
    enabled: open && Boolean(collectionId),
  })
  const activeId = selectedId ?? history.data?.items[0]?.id
  const detailKey = ['url-import', collectionId, activeId]
  const detail = useQuery({
    queryKey: detailKey,
    queryFn: () => fetchUrlImport(activeId!),
    enabled: open && Boolean(activeId),
    refetchInterval: (query) => query.state.data?.items.some((item) => item.asset && isPending(item.asset)) ? 1500 : false,
  })

  useEffect(() => {
    if (!detail.data) return
    const batch = detail.data
    queryClient.setQueryData<UrlImportPage>(['url-imports', collectionId], (previous) => previous ? {
      items: previous.items.map((item) => item.id === batch.id ? batch : item),
    } : previous)
    void queryClient.invalidateQueries({ queryKey: ['assets', collectionId] })
  }, [detail.data, collectionId, queryClient])

  const saveBatch = (batch: UrlImportBatch) => {
    queryClient.setQueryData(['url-import', collectionId, batch.id], batch)
    queryClient.setQueryData<UrlImportPage>(historyKey, (previous) => ({
      items: [batch, ...(previous?.items ?? []).filter((item) => item.id !== batch.id)]
        .sort((left, right) => right.createdAt.localeCompare(left.createdAt)).slice(0, 10),
    }))
    setSelectedId(batch.id)
    void queryClient.invalidateQueries({ queryKey: historyKey })
    void queryClient.invalidateQueries({ queryKey: ['assets', collectionId] })
  }
  const create = useMutation({
    mutationFn: (request: { urls: string[]; idempotencyKey: string }) =>
      createUrlImport(collectionId, { urls: request.urls }, request.idempotencyKey),
    retry: false,
    onSuccess: saveBatch,
  })
  const retry = useMutation({
    mutationFn: retryAsset,
    onSuccess: (asset) => {
      const updateBatch = (batch: UrlImportBatch): UrlImportBatch => ({
        ...batch,
        items: batch.items.map((item) => item.asset?.id === asset.id ? { ...item, asset } : item),
      })
      queryClient.setQueriesData<UrlImportBatch>({ queryKey: ['url-import', collectionId] }, (batch) => batch ? updateBatch(batch) : batch)
      queryClient.setQueryData<UrlImportPage>(historyKey, (page) => page ? { items: page.items.map(updateBatch) } : page)
      void queryClient.invalidateQueries({ queryKey: ['url-import', collectionId] })
      void queryClient.invalidateQueries({ queryKey: historyKey })
      void queryClient.invalidateQueries({ queryKey: ['assets', collectionId] })
      void queryClient.invalidateQueries({ queryKey: ['asset', asset.id] })
    },
  })
  const dispatch = useMutation({
    mutationFn: () => dispatchUrlImport(activeId!),
    onSuccess: saveBatch,
  })
  const needsDispatch = detail.data?.items.some((item) =>
    item.asset?.status === 'QUEUED' && item.asset.job && !item.asset.job.celeryTaskId)

  return (
    <Flex vertical gap="middle" style={{ minWidth: 0, overflowWrap: 'anywhere' }}>
      <Alert
        type="info"
        showIcon
        title="批量保存网页，逐条解析"
        description="一次粘贴多个链接，每行一个，最多 50 行，每个网址最长 2048 字符；空白行自动忽略。同一资料库已有的网页会复用，批内重复只创建一份资料。解析完成后，打开资料使用「AI 整理题目」。"
      />
      <Form layout="vertical" onFinish={() => {
        if (!urls.length || inputError || create.isPending) return
        if (create.isSuccess) {
          setSelectedId(create.data.id)
          void queryClient.invalidateQueries({ queryKey: ['url-import', collectionId, create.data.id] })
          return
        }
        create.mutate({ urls, idempotencyKey: draft.idempotencyKey })
      }}>
        <Form.Item
          label="网页链接（每行一个）"
          htmlFor={inputId}
          validateStatus={inputError ? 'error' : undefined}
          help={inputError}
          extra="原始输入会逐行保留；无效链接会单独说明原因，其他链接继续导入。"
        >
          <Input.TextArea
            id={inputId}
            autoSize={{ minRows: 6, maxRows: 12 }}
            value={draft.text}
            disabled={create.isPending}
            placeholder={'https://example.com/article-1\nhttps://example.com/article-2'}
            onChange={(event) => {
              const text = event.target.value
              if (text === draft.text) return
              setDraft({ text, idempotencyKey: crypto.randomUUID() })
              create.reset()
            }}
          />
        </Form.Item>
        <Flex vertical gap="small">
          <Space wrap>
            <Typography.Text type={tooMany ? 'danger' : 'secondary'} aria-live="polite">已输入 {urls.length}/50 行</Typography.Text>
            <Button type="primary" htmlType="submit" loading={create.isPending} disabled={!urls.length || Boolean(inputError) || !collectionId}>
              {create.isError ? '重试本次导入' : create.isSuccess ? '查看本次导入' : '导入链接'}
            </Button>
          </Space>
          {tooLongCount > 0 && !exceedsRequestLimit && <Alert type="warning" showIcon title={`${tooLongCount} 行超过 2048 字符，将标记为无效；其他链接照常导入。`} />}
          {create.isError && <Alert type="error" showIcon title="本次导入请求未确认" description={`${create.error.message}。点击重试会沿用本次请求标识，避免网络重发重复创建；修改输入将开始新的导入。`} />}
          {create.isSuccess && <Alert type="success" showIcon title="导入已提交，请查看逐条结果" description="解析在后台进行，可以关闭面板，稍后从历史记录继续查看。" />}
        </Flex>
      </Form>
      <Divider style={{ margin: 0 }} />
      <Typography.Title level={4} style={{ margin: 0 }}>最近 10 次导入</Typography.Title>
      {history.isLoading && <Skeleton active paragraph={{ rows: 2 }} />}
      {history.isError && <Alert type="error" showIcon title="导入历史加载失败" description={history.error.message} action={<Button onClick={() => void history.refetch()}>重试</Button>} />}
      {Boolean(history.data?.items.length) && <Select
        aria-label="选择导入批次"
        value={activeId}
        onChange={(id) => { setSelectedId(id); retry.reset(); dispatch.reset() }}
        options={history.data?.items.map((batch) => ({
          value: batch.id,
          label: `${new Date(batch.createdAt).toLocaleString()} · ${batch.items.length} 行 · ${batch.id.slice(0, 8)}`,
        }))}
        popupMatchSelectWidth
      />}
      {detail.isLoading && <Skeleton active />}
      {detail.isError && <Alert type="error" showIcon title="导入详情加载失败" description={detail.error.message} action={<Button onClick={() => void detail.refetch()}>重试</Button>} />}
      {retry.isError && <Alert type="error" showIcon title="重新解析失败" description={retry.error.message} />}
      {dispatch.isError && <Alert type="error" showIcon title="提交任务失败" description={dispatch.error.message} />}
      {detail.data && <>
        <ImportSummary batch={detail.data} />
        {needsDispatch && <Alert type="warning" showIcon title="部分资料尚未进入解析队列" description={
          <Flex vertical gap="small">
            <Typography.Text>可以继续提交这些任务，已完成及正在处理的资料会保留当前状态。</Typography.Text>
            <Button loading={dispatch.isPending} onClick={() => dispatch.mutate()}>继续提交未排队任务</Button>
          </Flex>
        } />}
        <Flex vertical gap="small" role="list" aria-label="逐条导入结果">
          {detail.data.items.map((item) => <div role="listitem" key={item.ordinal}>
            <ImportItemCard item={item} onOpenAsset={onOpenAsset} onRetry={(id) => retry.mutate(id)} retryBusy={retry.isPending} retryingId={retry.variables} />
          </div>)}
        </Flex>
      </>}
      {!history.isLoading && !history.isError && !activeId && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有批量导入记录，粘贴网页链接开始导入。" />}
    </Flex>
  )
}

export function BatchUrlImportDrawer({ open, collectionId, onClose, onOpenAsset }: BatchUrlImportDrawerProps) {
  return (
    <Drawer title="批量导入网页链接" open={open} onClose={onClose} size={760} styles={{ wrapper: { maxWidth: '100vw' } }}>
      <BatchUrlImportContent key={collectionId} open={open} collectionId={collectionId} onOpenAsset={onOpenAsset} />
    </Drawer>
  )
}
