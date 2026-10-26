import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  Button,
  Card,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  Layout,
  Modal,
  Pagination,
  Popconfirm,
  Radio,
  Skeleton,
  Space,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import {
  createCollection,
  createQuestion,
  createQuestionExtractionRun,
  createTextAsset,
  createUrlAsset,
  deleteAsset,
  deleteCollection,
  deleteNote,
  deleteQuestion,
  fetchAsset,
  fetchAssets,
  fetchCollections,
  fetchHealth,
  fetchQuestions,
  fetchLatestAssetExtractionRun,
  reviseAsset,
  retryAsset,
  retryQuestionExtractionRun,
  updateQuestion,
  uploadTextAsset,
  upsertAnswer,
  upsertNote,
  type AnswerFilter,
  type CollectionPurpose,
  type AssetSummary,
  type Question,
} from './api/client'
import { SafeMarkdown } from './components/SafeMarkdown'
import { ExtractionReviewDrawer } from './components/ExtractionReviewDrawer'
import { ProfileDrawer } from './components/ProfileDrawer'
import { RecommendationDrawer } from './components/RecommendationDrawer'
import { InterviewDrawer } from './components/InterviewDrawer'
import { BatchUrlImportDrawer } from './components/BatchUrlImportDrawer'
import { SourceStatistics } from './components/SourceStatistics'
import { AssetSourceMetadata } from './components/AssetSourceMetadata'
import { ExtractionQualityPanel } from './components/ExtractionQualityPanel'

interface CollectionForm {
  name: string
  purpose: CollectionPurpose
}

interface AssetForm {
  title: string
  content: string
  sourceFormat: 'PASTED_TEXT' | 'MARKDOWN'
}

interface QuestionForm {
  prompt: string
  answer?: string
}

interface ContentForm {
  content: string
}

interface UrlForm {
  url: string
  title?: string
}

const sourceLabels: Record<string, string> = {
  PASTED_TEXT: '粘贴文字',
  MARKDOWN: 'Markdown',
  TXT_FILE: 'TXT 文件',
  MARKDOWN_FILE: 'Markdown 文件',
  IMAGE: '图片',
  PDF: 'PDF',
  DOCX: 'Word 文档',
  DOC: '旧版 Word 文档',
  WEB_PAGE: '网页',
}

const statusLabels: Record<string, string> = {
  QUEUED: '等待解析',
  PROCESSING: '正在解析',
  READY: '已完成',
  PARTIAL_SUCCESS: '部分完成',
  WAITING_FOR_CONFIGURATION: '等待配置',
  FAILED: '解析失败',
}

const stageLabels: Record<string, string> = {
  STORED: '文件已保存',
  FETCHING: '正在抓取网页',
  RENDERING: '正在运行网页脚本',
  EXTRACTING: '正在提取文字',
  OCR: '正在识别图片',
  CONFIGURATION: '等待 OCR 配置',
  COMPLETE: '解析完成',
  FAILED: '解析失败',
}

const statusColors: Record<string, string> = {
  QUEUED: 'default',
  PROCESSING: 'processing',
  READY: 'success',
  PARTIAL_SUCCESS: 'warning',
  WAITING_FOR_CONFIGURATION: 'warning',
  FAILED: 'error',
}

const isPendingAsset = (asset: AssetSummary) =>
  asset.status === 'QUEUED' || asset.status === 'PROCESSING'

const canRetryAsset = (asset: AssetSummary) =>
  Boolean(
    asset.job &&
      ['FAILED', 'WAITING_FOR_CONFIGURATION', 'PARTIAL_SUCCESS'].includes(asset.job.status) &&
      asset.job.retryCount < 3,
  )

const formatLocator = (locator: Record<string, unknown>) => {
  if (typeof locator.page === 'number') return `PDF 第 ${locator.page} 页`
  if (typeof locator.paragraph === 'number') return `Word 第 ${locator.paragraph} 段`
  if (typeof locator.table === 'number') return `Word 第 ${locator.table} 个表格`
  return '未定位的内容块'
}

function App() {
  const queryClient = useQueryClient()
  const [collectionForm] = Form.useForm<CollectionForm>()
  const [assetForm] = Form.useForm<AssetForm>()
  const [urlForm] = Form.useForm<UrlForm>()
  const [questionForm] = Form.useForm<QuestionForm>()
  const [contentForm] = Form.useForm<ContentForm>()
  const [messageApi, messageContext] = message.useMessage()
  const [selectedCollectionId, setSelectedCollectionId] = useState<string | null>(null)
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null)
  const [isCreateCollectionOpen, setCreateCollectionOpen] = useState(false)
  const [isImportOpen, setImportOpen] = useState(false)
  const [isUrlImportOpen, setUrlImportOpen] = useState(false)
  const [isBatchUrlImportOpen, setBatchUrlImportOpen] = useState(false)
  const [isQuestionOpen, setQuestionOpen] = useState(false)
  const [dialogMode, setDialogMode] = useState<'question' | 'answer' | 'note'>('question')
  const [editingQuestion, setEditingQuestion] = useState<Question | null>(null)
  const [sourceChunkId, setSourceChunkId] = useState<string | null>(null)
  const [assetDraft, setAssetDraft] = useState('')
  const [isAssetEditing, setAssetEditing] = useState(false)
  const [activeTab, setActiveTab] = useState('assets')
  const [searchDraft, setSearchDraft] = useState('')
  const [search, setSearch] = useState('')
  const [answerFilter, setAnswerFilter] = useState<AnswerFilter>('ANY')
  const [page, setPage] = useState(1)
  const [extractionRunId, setExtractionRunId] = useState<string | null>(null)
  const [isReviewOpen, setReviewOpen] = useState(false)
  const [isProfileOpen, setProfileOpen] = useState(false)
  const [isRecommendationOpen, setRecommendationOpen] = useState(false)
  const [isInterviewOpen, setInterviewOpen] = useState(false)
  const reportedExtractionRunId = useRef<string | null>(null)

  const health = useQuery({
    queryKey: ['health'],
    queryFn: fetchHealth,
    refetchInterval: 30_000,
    retry: false,
  })
  const collections = useQuery({ queryKey: ['collections'], queryFn: fetchCollections })
  const activeCollectionId =
    collections.data?.items.some((item) => item.id === selectedCollectionId)
      ? selectedCollectionId
      : collections.data?.items[0]?.id ?? null
  const assets = useQuery({
    queryKey: ['assets', activeCollectionId],
    queryFn: () => fetchAssets(activeCollectionId!),
    enabled: Boolean(activeCollectionId),
    refetchInterval: (query) =>
      query.state.data?.items.some(isPendingAsset) ? 2_000 : false,
  })
  const assetDetail = useQuery({
    queryKey: ['asset', selectedAssetId],
    queryFn: () => fetchAsset(selectedAssetId!),
    enabled: Boolean(selectedAssetId),
    refetchInterval: (query) =>
      query.state.data && isPendingAsset(query.state.data) ? 2_000 : false,
  })
  const questions = useQuery({
    queryKey: ['questions', activeCollectionId, search, answerFilter, page],
    queryFn: () =>
      fetchQuestions({
        collectionId: activeCollectionId ?? undefined,
        q: search || undefined,
        answerState: answerFilter,
        page,
      }),
    enabled: Boolean(activeCollectionId),
  })
  const extractionRun = useQuery({
    queryKey: ['question-extraction-run', selectedAssetId, assetDetail.data?.currentVersion, extractionRunId],
    queryFn: () => fetchLatestAssetExtractionRun(selectedAssetId!),
    enabled: Boolean(selectedAssetId),
    refetchInterval: (query) =>
      query.state.data && ['QUEUED', 'PROCESSING'].includes(query.state.data.status)
        ? 2_000
        : false,
  })

  const selectedCollection = useMemo(
    () => collections.data?.items.find((item) => item.id === activeCollectionId),
    [activeCollectionId, collections.data],
  )

  const refreshCollections = () => queryClient.invalidateQueries({ queryKey: ['collections'] })
  const refreshAssets = () => queryClient.invalidateQueries({ queryKey: ['assets', activeCollectionId] })
  const refreshQuestions = () => queryClient.invalidateQueries({ queryKey: ['questions'] })

  const collectionCreate = useMutation({
    mutationFn: createCollection,
    onSuccess: async (created) => {
      await refreshCollections()
      setSelectedCollectionId(created.id)
      setCreateCollectionOpen(false)
      messageApi.success('资料库已创建')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const collectionDelete = useMutation({
    mutationFn: deleteCollection,
    onSuccess: async () => {
      await refreshCollections()
      messageApi.success('资料库已删除')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const textImport = useMutation({
    mutationFn: ({ collectionId, values }: { collectionId: string; values: AssetForm }) =>
      createTextAsset(collectionId, { ...values, title: values.title.trim() }),
    onSuccess: async () => {
      await refreshAssets()
      setImportOpen(false)
      messageApi.success('文字资料已保存')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const fileImport = useMutation({
    mutationFn: ({ collectionId, file }: { collectionId: string; file: File }) =>
      uploadTextAsset(collectionId, file),
    onSuccess: async () => {
      await refreshAssets()
      messageApi.success('文件已接收；需要解析的资料会在后台处理')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const urlImport = useMutation({
    mutationFn: ({ collectionId, values }: { collectionId: string; values: UrlForm }) =>
      createUrlAsset(collectionId, {
        url: values.url.trim(),
        title: values.title?.trim() || undefined,
      }),
    onSuccess: async () => {
      await refreshAssets()
      setUrlImportOpen(false)
      messageApi.success('网页链接已提交，正在后台提取正文')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const assetRetry = useMutation({
    mutationFn: retryAsset,
    onSuccess: async (_data, assetId) => {
      await Promise.all([
        refreshAssets(),
        queryClient.invalidateQueries({ queryKey: ['asset', assetId] }),
      ])
      messageApi.success('已重新提交解析任务')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const assetRevision = useMutation({
    mutationFn: ({ id, content, version }: { id: string; content: string; version: number }) =>
      reviseAsset(id, { content, expectedVersion: version }),
    onSuccess: async (_data, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['asset', variables.id] }),
        refreshAssets(),
      ])
      setAssetEditing(false)
      messageApi.success('原文已保存为新版本')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const assetDelete = useMutation({
    mutationFn: deleteAsset,
    onSuccess: async () => {
      setSelectedAssetId(null)
      await refreshAssets()
      messageApi.success('资料已删除，手写问答会保留')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const questionExtractionCreate = useMutation({
    mutationFn: createQuestionExtractionRun,
    onSuccess: (run) => {
      setExtractionRunId(run.id)
      void queryClient.invalidateQueries({ queryKey: ['question-extraction-run'] })
      reportedExtractionRunId.current = null
      messageApi.success('已提交自动整理任务；只提取原文已有的题目和答案')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const questionExtractionRetry = useMutation({
    mutationFn: retryQuestionExtractionRun,
    onSuccess: (run) => {
      setExtractionRunId(run.id)
      void queryClient.invalidateQueries({ queryKey: ['question-extraction-run'] })
      reportedExtractionRunId.current = null
      messageApi.success('已重新提交自动整理任务；已复核和人工修改的内容会保留')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const questionCreate = useMutation({
    mutationFn: createQuestion,
    onSuccess: async () => {
      await refreshQuestions()
      setQuestionOpen(false)
      setSourceChunkId(null)
      setActiveTab('questions')
      messageApi.success('题目已创建')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const questionUpdate = useMutation({
    mutationFn: ({ question, prompt }: { question: Question; prompt: string }) =>
      updateQuestion(question.id, { prompt, expectedVersion: question.version }),
    onSuccess: async () => {
      await refreshQuestions()
      closeEditDialog()
      messageApi.success('题目已更新')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const answerUpdate = useMutation({
    mutationFn: ({ question, content }: { question: Question; content: string }) =>
      upsertAnswer(question.id, {
        content,
        expectedVersion: question.answer?.version ?? 0,
      }),
    onSuccess: async () => {
      await refreshQuestions()
      closeEditDialog()
      messageApi.success('答案已保存')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const noteUpdate = useMutation({
    mutationFn: ({ question, content }: { question: Question; content: string }) =>
      upsertNote(question.id, { content, expectedVersion: question.note?.version ?? 0 }),
    onSuccess: async () => {
      await refreshQuestions()
      closeEditDialog()
      messageApi.success('备注已保存')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const noteDelete = useMutation({
    mutationFn: deleteNote,
    onSuccess: async () => {
      await refreshQuestions()
      messageApi.success('备注已删除')
    },
    onError: (error) => messageApi.error(error.message),
  })
  const questionDelete = useMutation({
    mutationFn: deleteQuestion,
    onSuccess: async () => {
      await refreshQuestions()
      messageApi.success('题目已删除')
    },
    onError: (error) => messageApi.error(error.message),
  })

  function closeEditDialog() {
    setEditingQuestion(null)
  }

  useEffect(() => {
    const run = extractionRun.data
    if (!run || run.id !== extractionRunId || run.id === reportedExtractionRunId.current) return
    if (run.status === 'SUCCEEDED' || run.status === 'PARTIAL_SUCCESS') {
      reportedExtractionRunId.current = run.id
      void queryClient.invalidateQueries({ queryKey: ['questions'] })
      messageApi.success(
        `自动整理完成：${run.answeredCount} 道有答案，${run.missingCount} 道无答案`,
      )
    } else if (run.status === 'FAILED' || run.status === 'WAITING_FOR_CONFIGURATION') {
      reportedExtractionRunId.current = run.id
      messageApi.error(run.errorMessage ?? '自动整理失败')
    }
  }, [extractionRun.data, extractionRunId, messageApi, queryClient])

  function openQuestionCreate(chunkId?: string) {
    setSourceChunkId(chunkId ?? null)
    setQuestionOpen(true)
  }

  function openEdit(question: Question, mode: 'question' | 'answer' | 'note') {
    setEditingQuestion(question)
    setDialogMode(mode)
  }

  const submitContentEdit = ({ content }: ContentForm) => {
    if (!editingQuestion) return
    if (dialogMode === 'question') {
      questionUpdate.mutate({ question: editingQuestion, prompt: content.trim() })
    } else if (dialogMode === 'answer') {
      answerUpdate.mutate({ question: editingQuestion, content })
    } else {
      noteUpdate.mutate({ question: editingQuestion, content })
    }
  }

  const renderAssets = () => (
    <section aria-label="资料">
      <div className="workspace-toolbar">
        <div>
          <Typography.Title level={2}>资料导入</Typography.Title>
          <Typography.Text type="secondary">
            支持文字、图片、PDF、Word，以及公开静态或 JavaScript 网页；文件上限 25 MiB，PDF 最多 100 页
          </Typography.Text>
        </div>
        <Space wrap>
          <Upload
            accept=".txt,.md,.markdown,.png,.jpg,.jpeg,.pdf,.docx,.doc"
            showUploadList={false}
            beforeUpload={(file) => {
              if (file.size > 25 * 1024 * 1024) {
                messageApi.error('文件不能超过 25 MiB')
                return Upload.LIST_IGNORE
              }
              if (/\.(png|jpe?g)$/i.test(file.name) && file.size > 10 * 1024 * 1024) {
                messageApi.error('OCR 图片不能超过 10 MiB')
                return Upload.LIST_IGNORE
              }
              if (activeCollectionId) {
                fileImport.mutate({ collectionId: activeCollectionId, file })
              }
              return Upload.LIST_IGNORE
            }}
          >
            <Button loading={fileImport.isPending}>导入文件</Button>
          </Upload>
          <Button onClick={() => setUrlImportOpen(true)}>导入网页链接</Button>
          <Button onClick={() => setBatchUrlImportOpen(true)}>批量导入链接</Button>
          <Button type="primary" onClick={() => setImportOpen(true)}>
            粘贴文字
          </Button>
        </Space>
      </div>
      {assets.isLoading && <Skeleton active paragraph={{ rows: 5 }} />}
      {assets.isError && (
        <Alert type="error" showIcon title="资料加载失败" description={assets.error.message} />
      )}
      {assets.data?.items.length === 0 && (
        <Empty description="这个资料库还没有资料">
          <Button type="primary" onClick={() => setImportOpen(true)}>
            添加第一份资料
          </Button>
        </Empty>
      )}
      <div className="asset-grid">
        {assets.data?.items.map((asset) => (
          <Card
            key={asset.id}
            hoverable
            title={asset.title}
            extra={
              <Space size={4} wrap>
                <Tag>{sourceLabels[asset.sourceFormat]}</Tag>
                <Tag color={statusColors[asset.status]}>{statusLabels[asset.status]}</Tag>
              </Space>
            }
            onClick={() => setSelectedAssetId(asset.id)}
          >
            <div className="asset-meta">
              {asset.job && (
                <Typography.Text type="secondary">
                  {stageLabels[asset.job.stage] ?? asset.job.stage}
                  {asset.job.totalUnits
                    ? ` · ${asset.job.processedUnits}/${asset.job.totalUnits}`
                    : ''}
                </Typography.Text>
              )}
              {asset.job?.errorMessage && (
                <Typography.Text type="danger">{asset.job.errorMessage}</Typography.Text>
              )}
              <Typography.Text type="secondary">
                {asset.currentVersion > 0 ? `版本 ${asset.currentVersion} · ` : ''}
                {new Date(asset.updatedAt).toLocaleString()}
              </Typography.Text>
              {canRetryAsset(asset) && (
                <Button
                  size="small"
                  loading={assetRetry.isPending && assetRetry.variables === asset.id}
                  onClick={(event) => {
                    event.stopPropagation()
                    assetRetry.mutate(asset.id)
                  }}
                >
                  重试解析（{asset.job!.retryCount}/3）
                </Button>
              )}
            </div>
          </Card>
        ))}
      </div>
    </section>
  )

  const renderQuestions = () => (
    <section aria-label="问答">
      <div className="workspace-toolbar">
        <div>
          <Typography.Title level={2}>问答复习</Typography.Title>
          <Typography.Text type="secondary">搜索范围包含题干和答案</Typography.Text>
        </div>
        <Button type="primary" onClick={() => openQuestionCreate()}>
          新建题目
        </Button>
      </div>
      <div className="question-controls">
        <Input.Search
          className="question-search"
          allowClear
          value={searchDraft}
          placeholder="搜索 Python、MySQL、缓存穿透……"
          enterButton="搜索"
          onChange={(event) => setSearchDraft(event.target.value)}
          onSearch={(value) => {
            setSearch(value.trim())
            setPage(1)
          }}
        />
        <Radio.Group
          aria-label="按答案状态筛选"
          optionType="button"
          buttonStyle="solid"
          value={answerFilter}
          onChange={(event) => {
            setAnswerFilter(event.target.value as AnswerFilter)
            setPage(1)
          }}
        >
          <Radio.Button value="ANY">
            全部 {(questions.data?.answeredCount ?? 0) + (questions.data?.missingCount ?? 0)}
          </Radio.Button>
          <Radio.Button value="ANSWERED">有答案 {questions.data?.answeredCount ?? 0}</Radio.Button>
          <Radio.Button value="MISSING">无答案 {questions.data?.missingCount ?? 0}</Radio.Button>
        </Radio.Group>
      </div>
      {questions.isLoading && <Skeleton active paragraph={{ rows: 8 }} />}
      {questions.isError && (
        <Alert type="error" showIcon title="问答加载失败" description={questions.error.message} />
      )}
      {questions.data?.items.length === 0 && (
        <Empty
          description={
            search
              ? '没有匹配的问答'
              : answerFilter === 'ANSWERED'
                ? '还没有带答案的问题'
                : answerFilter === 'MISSING'
                  ? '没有待补充答案的问题'
                  : '还没有问答'
          }
        />
      )}
      <div className="question-list">
        {questions.data?.items.map((question, index) => (
          <article className="question-card" key={question.id}>
            <div className="question-heading">
              <span className="question-index">Q{(page - 1) * 20 + index + 1}</span>
              <Tag
                color={
                  question.sourceType === 'SOURCE'
                    ? 'blue'
                    : question.sourceType === 'SOURCE_EXTRACTED'
                      ? 'purple'
                      : 'default'
                }
              >
                {question.sourceType === 'SOURCE'
                  ? '来自资料'
                  : question.sourceType === 'SOURCE_EXTRACTED'
                    ? '自动提取·待复核'
                  : question.sourceType === 'SOURCE_REMOVED'
                    ? '来源已删除'
                    : '手动整理'}
              </Tag>
              <Tag color={question.answerState === 'MISSING' ? 'orange' : 'green'}>
                {question.answerState === 'MISSING' ? '无答案' : '有答案'}
              </Tag>
            </div>
            <SafeMarkdown className="question-text">{question.prompt}</SafeMarkdown>
            <SourceStatistics statistics={question.sourceStatistics} />
            <Divider plain>
              答案
            </Divider>
            {question.answer ? (
              <SafeMarkdown className="answer-text">{question.answer.content}</SafeMarkdown>
            ) : (
              <Typography.Text type="secondary">暂未填写答案</Typography.Text>
            )}
            {question.note && (
              <div className="note-box">
                <strong>我的备注</strong>
                <p>{question.note.content}</p>
              </div>
            )}
            <div className="question-actions">
              <Button type="link" onClick={() => openEdit(question, 'question')}>编辑题目</Button>
              <Button type="link" onClick={() => openEdit(question, 'answer')}>编辑答案</Button>
              <Button type="link" onClick={() => openEdit(question, 'note')}>
                {question.note ? '编辑备注' : '添加备注'}
              </Button>
              {question.note && (
                <Popconfirm title="删除这条备注？" onConfirm={() => noteDelete.mutate(question.id)}>
                  <Button type="link" danger>删除备注</Button>
                </Popconfirm>
              )}
              <Popconfirm title="删除这道题？" onConfirm={() => questionDelete.mutate(question.id)}>
                <Button type="link" danger>删除题目</Button>
              </Popconfirm>
            </div>
          </article>
        ))}
      </div>
      {(questions.data?.total ?? 0) > 20 && (
        <Pagination
          current={page}
          pageSize={20}
          total={questions.data?.total}
          showSizeChanger={false}
          onChange={setPage}
        />
      )}
    </section>
  )

  return (
    <Layout className="app-shell">
      {messageContext}
      <header className="app-header">
        <div>
          <Typography.Title level={1}>面经综合助手</Typography.Title>
          <Typography.Text type="secondary">把分散资料整理成自己的复习问答</Typography.Text>
        </div>
        <div className="header-actions">
          <Space wrap>
            <Button onClick={() => setProfileOpen(true)}>我的画像</Button>
            <Button type="primary" onClick={() => setRecommendationOpen(true)}>我的复习清单</Button>
            <Button onClick={() => setInterviewOpen(true)}>模拟面试</Button>
          </Space>
          <div className="service-state" role="status" aria-live="polite">
            <span className={health.data ? 'status-dot status-ok' : 'status-dot status-error'} />
            {health.data ? '本地服务正常' : health.isError ? '本地服务异常' : '正在检查服务'}
          </div>
        </div>
      </header>

      <Layout className="workspace-layout">
        <aside className="collection-panel" aria-label="资料库列表">
          <div className="panel-heading">
            <div>
              <Typography.Title level={2}>资料库</Typography.Title>
              <Typography.Text type="secondary">面经与内部知识分开管理</Typography.Text>
            </div>
            <Button type="primary" onClick={() => setCreateCollectionOpen(true)}>新建</Button>
          </div>
          {collections.isLoading && <Skeleton active paragraph={{ rows: 4 }} />}
          {collections.isError && (
            <Alert
              type="error"
              showIcon
              title="资料库加载失败"
              description={collections.error.message}
              action={<Button onClick={() => collections.refetch()}>重试</Button>}
            />
          )}
          {collections.data?.items.length === 0 && (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有资料库">
              <Button type="primary" onClick={() => setCreateCollectionOpen(true)}>
                创建第一个资料库
              </Button>
            </Empty>
          )}
          <div className="collection-list">
            {(collections.data?.items ?? []).map((item) => (
              <div
                key={item.id}
                className={item.id === activeCollectionId ? 'collection-item selected' : 'collection-item'}
              >
                <button className="collection-select" onClick={() => setSelectedCollectionId(item.id)}>
                  <strong>{item.name}</strong>
                  <Tag>{item.purpose === 'KNOWLEDGE_BASE' ? '知识库' : '面经'}</Tag>
                </button>
                <div className="collection-actions">
                  <Popconfirm
                    title="删除这个资料库？"
                    description="资料库内有内容时请先删除内容。"
                    okText="删除"
                    cancelText="取消"
                    onConfirm={() => collectionDelete.mutate(item.id)}
                  >
                    <Button type="link" danger aria-label={`删除资料库 ${item.name}`}>删除</Button>
                  </Popconfirm>
                </div>
              </div>
            ))}
          </div>
        </aside>

        <main className="content-panel">
          {selectedCollection ? (
            <>
              <div className="collection-context">
                <Typography.Text type="secondary">当前资料库</Typography.Text>
                <Typography.Title level={2}>{selectedCollection.name}</Typography.Title>
              </div>
              <Tabs
                activeKey={activeTab}
                onChange={setActiveTab}
                items={[
                  { key: 'assets', label: '资料', children: renderAssets() },
                  { key: 'questions', label: '问答', children: renderQuestions() },
                ]}
              />
            </>
          ) : (
            <div className="content-empty">
              <Typography.Title level={2}>从一个资料库开始</Typography.Title>
              <Typography.Paragraph type="secondary">
                资料库用于归类面经和内部知识，然后可以导入文字并整理问答。
              </Typography.Paragraph>
            </div>
          )}
        </main>
      </Layout>

      <Modal
        title="新建资料库"
        open={isCreateCollectionOpen}
        okText="创建"
        cancelText="取消"
        confirmLoading={collectionCreate.isPending}
        afterOpenChange={(open) => {
          if (open) collectionForm.resetFields()
        }}
        onOk={() => collectionForm.submit()}
        onCancel={() => setCreateCollectionOpen(false)}
        forceRender
      >
        <Form<CollectionForm>
          form={collectionForm}
          layout="vertical"
          initialValues={{ purpose: 'INTERVIEW_EXPERIENCE' }}
          onFinish={(values) => collectionCreate.mutate({ ...values, name: values.name.trim() })}
        >
          <Form.Item
            label="资料库名称"
            name="name"
            rules={[
              { required: true, whitespace: true, message: '请输入资料库名称' },
              { max: 120, message: '名称最多 120 个字符' },
            ]}
          >
            <Input autoFocus placeholder="例如：Python 后端面经" />
          </Form.Item>
          <Form.Item label="资料类型" name="purpose">
            <Radio.Group>
              <Radio value="INTERVIEW_EXPERIENCE">面经</Radio>
              <Radio value="KNOWLEDGE_BASE">内部知识库</Radio>
            </Radio.Group>
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="粘贴文字资料"
        open={isImportOpen}
        width={720}
        okText="保存资料"
        cancelText="取消"
        confirmLoading={textImport.isPending}
        afterOpenChange={(open) => {
          if (open) assetForm.resetFields()
        }}
        onOk={() => assetForm.submit()}
        onCancel={() => setImportOpen(false)}
        forceRender
      >
        <Form<AssetForm>
          form={assetForm}
          layout="vertical"
          initialValues={{ sourceFormat: 'PASTED_TEXT' }}
          onFinish={(values) => {
            if (activeCollectionId) textImport.mutate({ collectionId: activeCollectionId, values })
          }}
        >
          <Form.Item label="标题" name="title" rules={[{ required: true, whitespace: true }]}>
            <Input placeholder="例如：某公司一面记录" />
          </Form.Item>
          <Form.Item label="格式" name="sourceFormat">
            <Radio.Group>
              <Radio value="PASTED_TEXT">纯文字</Radio>
              <Radio value="MARKDOWN">Markdown</Radio>
            </Radio.Group>
          </Form.Item>
          <Form.Item label="原文" name="content" rules={[{ required: true, whitespace: true }]}>
            <Input.TextArea rows={14} showCount maxLength={1024 * 1024} />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title="导入网页链接"
        open={isUrlImportOpen}
        width={640}
        okText="开始导入"
        cancelText="取消"
        confirmLoading={urlImport.isPending}
        afterOpenChange={(open) => {
          if (open) urlForm.resetFields()
        }}
        onOk={() => urlForm.submit()}
        onCancel={() => setUrlImportOpen(false)}
        forceRender
      >
        <Alert
          className="form-hint"
          type="info"
          showIcon
          title="支持公开静态网页和 JavaScript 动态网页"
          description="系统会自动选择提取方式。需要登录、验证码，或链接没有记录当前章节时，请改用粘贴文字、截图或文件上传。"
        />
        <Form<UrlForm>
          form={urlForm}
          layout="vertical"
          onFinish={(values) => {
            if (activeCollectionId) urlImport.mutate({ collectionId: activeCollectionId, values })
          }}
        >
          <Form.Item
            label="网页地址"
            name="url"
            rules={[
              { required: true, whitespace: true, message: '请输入网页地址' },
              { type: 'url', message: '请输入完整的 http:// 或 https:// 地址' },
            ]}
          >
            <Input autoFocus placeholder="https://example.com/interview" />
          </Form.Item>
          <Form.Item label="标题（可选）" name="title">
            <Input maxLength={200} placeholder="留空时使用网页标题或域名" />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        classNames={{ root: 'asset-drawer' }}
        title={assetDetail.data?.title ?? '资料详情'}
        size={720}
        open={Boolean(selectedAssetId)}
        onClose={() => {
          setSelectedAssetId(null)
          setAssetEditing(false)
        }}
        extra={
          assetDetail.data && (
            <Space>
              <Button
                type="primary"
                disabled={
                  assetDetail.data.currentVersion === 0 ||
                  !['READY', 'PARTIAL_SUCCESS'].includes(assetDetail.data.status) ||
                  (extractionRun.data?.assetId === assetDetail.data.id &&
                    ['QUEUED', 'PROCESSING'].includes(extractionRun.data.status))
                }
                loading={questionExtractionCreate.isPending}
                onClick={() => questionExtractionCreate.mutate(assetDetail.data!.id)}
              >
                AI 自动整理
              </Button>
              <Button
                disabled={assetDetail.data.currentVersion === 0}
                onClick={() => {
                  openQuestionCreate(assetDetail.data?.chunks[0]?.id)
                }}
              >
                手动建题
              </Button>
              <Button
                disabled={assetDetail.data.currentVersion === 0}
                onClick={() => {
                  setAssetDraft(assetDetail.data!.content)
                  setAssetEditing(true)
                }}
              >
                修订原文
              </Button>
              <Popconfirm
                title="删除这份资料？"
                description="来源关联会被清除，已整理的问答会保留。"
                onConfirm={() => assetDelete.mutate(assetDetail.data!.id)}
              >
                <Button danger>删除</Button>
              </Popconfirm>
            </Space>
          )
        }
      >
        {assetDetail.isLoading && <Skeleton active />}
        {assetDetail.isError && <Alert type="error" title={assetDetail.error.message} />}
        {assetDetail.data && (
          <>
            <Space wrap>
              <Tag>{sourceLabels[assetDetail.data.sourceFormat]}</Tag>
              <Tag color={statusColors[assetDetail.data.status]}>
                {statusLabels[assetDetail.data.status]}
              </Tag>
              {assetDetail.data.currentVersion > 0 && (
                <Tag>当前版本 {assetDetail.data.currentVersion}</Tag>
              )}
              <Typography.Text type="secondary">
                共 {assetDetail.data.versions.length} 个版本
              </Typography.Text>
            </Space>
            {assetDetail.data.sourceUrl && (
              <Typography.Paragraph className="source-metadata">
                来源：{' '}
                <Typography.Link
                  href={assetDetail.data.sourceUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {assetDetail.data.sourceUrl}
                </Typography.Link>
                {assetDetail.data.fetchedAt && (
                  <> · 抓取于 {new Date(assetDetail.data.fetchedAt).toLocaleString()}</>
                )}
              </Typography.Paragraph>
            )}
            <AssetSourceMetadata
              key={`${assetDetail.data.id}-${assetDetail.data.sourceMetadataVersion}`}
              asset={assetDetail.data}
              notify={(kind, text) => { if (kind === 'success') messageApi.success(text); else messageApi.error(text) }}
            />
            {assetDetail.data.originalFilename && (
              <Typography.Paragraph className="source-metadata" type="secondary">
                原文件：{assetDetail.data.originalFilename}
                {assetDetail.data.byteSize
                  ? ` · ${(assetDetail.data.byteSize / 1024).toFixed(1)} KiB`
                  : ''}
              </Typography.Paragraph>
            )}
            {assetDetail.data.job && isPendingAsset(assetDetail.data) && (
              <Alert
                className="asset-state-alert"
                type="info"
                showIcon
                title={stageLabels[assetDetail.data.job.stage] ?? assetDetail.data.job.stage}
                description="页面会自动刷新解析状态。"
              />
            )}
            {assetDetail.data.job?.errorMessage && (
              <Alert
                className="asset-state-alert"
                type={assetDetail.data.status === 'FAILED' ? 'error' : 'warning'}
                showIcon
                title={assetDetail.data.job.errorMessage}
                description={
                  canRetryAsset(assetDetail.data) ? (
                    <Button
                      size="small"
                      loading={assetRetry.isPending}
                      onClick={() => assetRetry.mutate(assetDetail.data!.id)}
                    >
                      重新解析（{assetDetail.data.job!.retryCount}/3）
                    </Button>
                  ) : undefined
                }
              />
            )}
            {extractionRun.data?.assetId === assetDetail.data.id && (
              <Alert
                className="asset-state-alert"
                type={
                  extractionRun.data.status === 'FAILED'
                    ? 'error'
                    : extractionRun.data.status === 'WAITING_FOR_CONFIGURATION'
                      ? 'warning'
                    : extractionRun.data.status === 'PARTIAL_SUCCESS'
                      ? 'warning'
                    : extractionRun.data.status === 'SUCCEEDED'
                      ? 'success'
                      : 'info'
                }
                showIcon
                title={
                  extractionRun.data.status === 'SUCCEEDED'
                    ? '题目自动整理完成'
                    : extractionRun.data.status === 'PARTIAL_SUCCESS'
                      ? '题目自动整理部分完成'
                    : extractionRun.data.status === 'WAITING_FOR_CONFIGURATION'
                      ? '等待 GLM 配置'
                      : extractionRun.data.status === 'FAILED'
                        ? '题目自动整理失败'
                        : '正在自动整理题目'
                }
                description={
                  <Space orientation="vertical" align="start">
                    <span>
                      {extractionRun.data.status === 'PARTIAL_SUCCESS'
                        ? `原始提取 ${extractionRun.data.questionCount} 条记录：${extractionRun.data.answeredCount} 条有答案，${extractionRun.data.missingCount} 条无答案。${extractionRun.data.errorMessage ?? '部分内容未能识别'}。当前复核状态见下方质量概览。`
                        : extractionRun.data.status === 'SUCCEEDED'
                          ? `原始提取 ${extractionRun.data.questionCount} 条记录：${extractionRun.data.answeredCount} 条有答案，${extractionRun.data.missingCount} 条无答案。当前复核状态见下方质量概览。`
                          : extractionRun.data.errorMessage ?? '系统只会提取原文已有内容，不会生成缺失答案。'}
                    </span>
                    {['SUCCEEDED', 'PARTIAL_SUCCESS'].includes(extractionRun.data.status) && (
                      <Button type="primary" size="small" onClick={() => setReviewOpen(true)}>
                        查看质量与原文复核
                      </Button>
                    )}
                    {['FAILED', 'PARTIAL_SUCCESS', 'WAITING_FOR_CONFIGURATION'].includes(
                      extractionRun.data.status,
                    ) &&
                      extractionRun.data.retryCount < 3 && (
                        <Button
                          size="small"
                          loading={questionExtractionRetry.isPending}
                          onClick={() => questionExtractionRetry.mutate(extractionRun.data!.id)}
                        >
                          重试整理（{extractionRun.data.retryCount}/3）
                        </Button>
                      )}
                  </Space>
                }
              />
            )}
            {extractionRun.data?.assetId === assetDetail.data.id &&
              !['QUEUED', 'PROCESSING'].includes(extractionRun.data.status) && (
                <ExtractionQualityPanel key={`${extractionRun.data.id}-${extractionRun.data.updatedAt}`} runId={extractionRun.data.id} />
              )}
            {(assetDetail.data.job?.unitFailures.length ?? 0) > 0 && (
              <div className="unit-failures" role="status">
                <Typography.Text strong>尚未识别的位置</Typography.Text>
                <ul>
                  {assetDetail.data.job!.unitFailures.map((failure, index) => (
                    <li key={`${failure.code}-${index}`}>
                      {formatLocator(failure.locator)}：{failure.message}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <Divider />
            {isAssetEditing ? (
              <>
                <Input.TextArea
                  rows={20}
                  value={assetDraft}
                  onChange={(event) => setAssetDraft(event.target.value)}
                />
                <Space className="drawer-actions">
                  <Button onClick={() => setAssetEditing(false)}>取消</Button>
                  <Button
                    type="primary"
                    loading={assetRevision.isPending}
                    onClick={() =>
                      assetRevision.mutate({
                        id: assetDetail.data!.id,
                        content: assetDraft,
                        version: assetDetail.data!.currentVersion,
                      })
                    }
                  >
                    保存为新版本
                  </Button>
                </Space>
              </>
            ) : assetDetail.data.currentVersion > 0 ? (
              <div className="source-content">{assetDetail.data.content}</div>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="解析完成后将在这里显示文字" />
            )}
          </>
        )}
      </Drawer>

      <ExtractionReviewDrawer
        key={extractionRun.data?.id ?? 'no-run'}
        open={isReviewOpen}
        runId={extractionRun.data?.id ?? null}
        chunks={assetDetail.data?.chunks ?? []}
        onClose={() => setReviewOpen(false)}
        onQuestionsChanged={() => {
          void refreshQuestions()
        }}
        notify={(kind, text) => {
          if (kind === 'success') messageApi.success(text)
          else messageApi.error(text)
        }}
      />

      <ProfileDrawer
        open={isProfileOpen}
        onClose={() => setProfileOpen(false)}
        notify={(kind, text) => {
          if (kind === 'success') messageApi.success(text)
          else messageApi.error(text)
        }}
      />

      <RecommendationDrawer
        open={isRecommendationOpen}
        onClose={() => setRecommendationOpen(false)}
        notify={(kind, text) => {
          if (kind === 'success') messageApi.success(text)
          else messageApi.error(text)
        }}
      />

      <InterviewDrawer open={isInterviewOpen} onClose={() => setInterviewOpen(false)} notify={(kind, text) => {
        if (kind === 'success') messageApi.success(text)
        else messageApi.error(text)
      }} />

      <Modal
        title={sourceChunkId ? '从资料整理题目' : '新建题目'}
        open={isQuestionOpen}
        width={720}
        okText="创建"
        cancelText="取消"
        confirmLoading={questionCreate.isPending}
        afterOpenChange={(open) => {
          if (open) questionForm.resetFields()
        }}
        onOk={() => questionForm.submit()}
        onCancel={() => {
          setQuestionOpen(false)
          setSourceChunkId(null)
        }}
        forceRender
      >
        {sourceChunkId && <Alert type="info" showIcon title="这道题会保留当前资料段落作为来源" />}
        <Form<QuestionForm>
          form={questionForm}
          layout="vertical"
          onFinish={(values) =>
            questionCreate.mutate({
              collectionId: activeCollectionId ?? undefined,
              sourceChunkId: sourceChunkId ?? undefined,
              prompt: values.prompt,
              answer: values.answer || undefined,
            })
          }
        >
          <Form.Item label="问题" name="prompt" rules={[{ required: true, whitespace: true }]}>
            <Input.TextArea rows={4} placeholder="输入问题，支持 Markdown" />
          </Form.Item>
          <Form.Item label="答案（可稍后填写）" name="answer">
            <Input.TextArea rows={8} placeholder="支持 Markdown 和代码块" />
          </Form.Item>
        </Form>
      </Modal>

      <Modal
        title={dialogMode === 'question' ? '编辑题目' : dialogMode === 'answer' ? '编辑答案' : '编辑备注'}
        open={Boolean(editingQuestion)}
        width={720}
        okText="保存"
        cancelText="取消"
        confirmLoading={questionUpdate.isPending || answerUpdate.isPending || noteUpdate.isPending}
        afterOpenChange={(open) => {
          if (!open || !editingQuestion) return
          contentForm.setFieldsValue({
            content:
              dialogMode === 'question'
                ? editingQuestion.prompt
                : dialogMode === 'answer'
                  ? editingQuestion.answer?.content ?? ''
                  : editingQuestion.note?.content ?? '',
          })
        }}
        onOk={() => contentForm.submit()}
        onCancel={closeEditDialog}
        forceRender
      >
        <Form<ContentForm> form={contentForm} layout="vertical" onFinish={submitContentEdit}>
          <Form.Item
            label={dialogMode === 'question' ? '题目' : dialogMode === 'answer' ? '答案' : '私人备注'}
            name="content"
            rules={[{ required: true, whitespace: true, message: '内容不能为空' }]}
          >
            <Input.TextArea rows={dialogMode === 'note' ? 6 : 12} />
          </Form.Item>
        </Form>
      </Modal>
      {activeCollectionId && <BatchUrlImportDrawer
        open={isBatchUrlImportOpen}
        collectionId={activeCollectionId}
        onClose={() => setBatchUrlImportOpen(false)}
        onOpenAsset={(id) => {
          setBatchUrlImportOpen(false)
          setSelectedAssetId(id)
        }}
      />}
    </Layout>
  )
}

export default App
