export interface HealthCheck {
  status: string
  checks: Record<string, string>
}

export type CollectionPurpose = 'INTERVIEW_EXPERIENCE' | 'KNOWLEDGE_BASE'

export interface Collection {
  id: string
  name: string
  purpose: CollectionPurpose
  version: number
  createdAt: string
  updatedAt: string
}

export interface CollectionPage {
  items: Collection[]
}

export type SourceFormat =
  | 'PASTED_TEXT'
  | 'MARKDOWN'
  | 'TXT_FILE'
  | 'MARKDOWN_FILE'
  | 'IMAGE'
  | 'PDF'
  | 'DOCX'
  | 'DOC'
  | 'WEB_PAGE'

export type AssetStatus =
  | 'QUEUED'
  | 'PROCESSING'
  | 'READY'
  | 'PARTIAL_SUCCESS'
  | 'WAITING_FOR_CONFIGURATION'
  | 'FAILED'

export interface IngestionUnitFailure {
  code: string
  message: string
  locator: Record<string, unknown>
}

export interface IngestionJob {
  id: string
  status: 'QUEUED' | 'PROCESSING' | 'SUCCEEDED' | 'PARTIAL_SUCCESS' | 'WAITING_FOR_CONFIGURATION' | 'FAILED'
  stage: string
  processedUnits: number
  totalUnits: number | null
  retryCount: number
  errorCode: string | null
  errorMessage: string | null
  unitFailures: IngestionUnitFailure[]
  createdAt: string
  updatedAt: string
  startedAt: string | null
  finishedAt: string | null
}

export interface AssetSummary {
  id: string
  collectionId: string
  title: string
  sourceFormat: SourceFormat
  originalFilename: string | null
  sourceUrl: string | null
  contentType: string | null
  byteSize: number | null
  fetchedAt: string | null
  status: AssetStatus
  currentVersion: number
  job: IngestionJob | null
  createdAt: string
  updatedAt: string
}

export interface AssetVersion {
  version: number
  content: string
  createdAt: string
}

export interface TextChunk {
  id: string
  ordinal: number
  content: string
  startOffset: number
  endOffset: number
  blockType: string
  locator: Record<string, unknown>
}

export interface AssetDetail extends AssetSummary {
  content: string
  versions: AssetVersion[]
  chunks: TextChunk[]
}

export interface AssetPage {
  items: AssetSummary[]
}

export interface Answer {
  id: string
  content: string
  sourceType: 'USER_WRITTEN'
  version: number
  updatedAt: string
}

export interface QuestionNote {
  id: string
  content: string
  version: number
  updatedAt: string
}

export interface Question {
  id: string
  collectionId: string | null
  sourceChunkId: string | null
  sourceType: 'USER_WRITTEN' | 'SOURCE' | 'SOURCE_REMOVED'
  prompt: string
  version: number
  answer: Answer | null
  answerState: 'MISSING' | 'SOURCE_ANSWER'
  note: QuestionNote | null
  createdAt: string
  updatedAt: string
}

export interface QuestionPage {
  items: Question[]
  page: number
  pageSize: number
  total: number
  answeredCount: number
  missingCount: number
}

export type AnswerFilter = 'ANY' | 'ANSWERED' | 'MISSING'

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    let message = `请求失败（HTTP ${response.status}）`
    try {
      const payload = (await response.json()) as {
        detail?: string | Array<{ msg?: string }>
      }
      if (typeof payload.detail === 'string') message = payload.detail
      if (Array.isArray(payload.detail) && payload.detail[0]?.msg) {
        message = payload.detail[0].msg.replace(/^Value error, /, '')
      }
    } catch {
      // 非 JSON 错误响应沿用状态码提示。
    }
    throw new Error(message)
  }
  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const jsonRequest = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

const idempotentCreate = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    'Idempotency-Key': crypto.randomUUID(),
  },
  body: JSON.stringify(body),
})

export const fetchHealth = () => apiRequest<HealthCheck>('/api/v1/health/ready')
export const fetchCollections = () => apiRequest<CollectionPage>('/api/v1/collections')

export const createCollection = (input: {
  name: string
  purpose: CollectionPurpose
}) => apiRequest<Collection>('/api/v1/collections', idempotentCreate(input))

export const deleteCollection = (id: string) =>
  apiRequest<void>(`/api/v1/collections/${id}`, { method: 'DELETE' })

export const fetchAssets = (collectionId: string) =>
  apiRequest<AssetPage>(`/api/v1/collections/${collectionId}/assets`)

export const fetchAsset = (assetId: string) =>
  apiRequest<AssetDetail>(`/api/v1/assets/${assetId}`)

export const createTextAsset = (
  collectionId: string,
  input: { title: string; content: string; sourceFormat: 'PASTED_TEXT' | 'MARKDOWN' },
) =>
  apiRequest<AssetSummary>(
    `/api/v1/collections/${collectionId}/assets/text`,
    idempotentCreate(input),
  )

export const uploadTextAsset = (collectionId: string, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return apiRequest<AssetSummary>(`/api/v1/collections/${collectionId}/assets/upload`, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: form,
  })
}

export const createUrlAsset = (
  collectionId: string,
  input: { url: string; title?: string },
) =>
  apiRequest<AssetSummary>(
    `/api/v1/collections/${collectionId}/assets/url`,
    idempotentCreate(input),
  )

export const retryAsset = (assetId: string) =>
  apiRequest<AssetSummary>(`/api/v1/assets/${assetId}/retry`, { method: 'POST' })

export const reviseAsset = (
  assetId: string,
  input: { content: string; expectedVersion: number },
) => apiRequest<AssetSummary>(`/api/v1/assets/${assetId}/content`, jsonRequest('PUT', input))

export const deleteAsset = (assetId: string) =>
  apiRequest<void>(`/api/v1/assets/${assetId}`, { method: 'DELETE' })

export const fetchQuestions = (input: {
  collectionId?: string
  q?: string
  answerState?: AnswerFilter
  page?: number
  pageSize?: number
}) => {
  const params = new URLSearchParams()
  if (input.collectionId) params.set('collectionId', input.collectionId)
  if (input.q) params.set('q', input.q)
  if (input.answerState) params.set('answerState', input.answerState)
  params.set('page', String(input.page ?? 1))
  params.set('pageSize', String(input.pageSize ?? 20))
  return apiRequest<QuestionPage>(`/api/v1/questions?${params.toString()}`)
}

export const createQuestion = (input: {
  collectionId?: string
  sourceChunkId?: string
  prompt: string
  answer?: string
}) => apiRequest<Question>('/api/v1/questions', idempotentCreate(input))

export const updateQuestion = (
  questionId: string,
  input: { prompt: string; expectedVersion: number },
) => apiRequest<Question>(`/api/v1/questions/${questionId}`, jsonRequest('PUT', input))

export const upsertAnswer = (
  questionId: string,
  input: { content: string; expectedVersion: number },
) => apiRequest<Answer>(`/api/v1/questions/${questionId}/answer`, jsonRequest('PUT', input))

export const upsertNote = (
  questionId: string,
  input: { content: string; expectedVersion: number },
) => apiRequest<QuestionNote>(`/api/v1/questions/${questionId}/note`, jsonRequest('PUT', input))

export const deleteNote = (questionId: string) =>
  apiRequest<void>(`/api/v1/questions/${questionId}/note`, { method: 'DELETE' })

export const deleteQuestion = (questionId: string) =>
  apiRequest<void>(`/api/v1/questions/${questionId}`, { method: 'DELETE' })
