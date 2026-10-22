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
  collectionId: string | null
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
  sourceType: 'USER_WRITTEN' | 'SOURCE_EXTRACTED'
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
  sourceType: 'USER_WRITTEN' | 'SOURCE' | 'SOURCE_EXTRACTED' | 'SOURCE_REMOVED'
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

export interface QuestionExtractionRun {
  id: string
  assetId: string
  assetVersionId: string
  status: 'QUEUED' | 'PROCESSING' | 'SUCCEEDED' | 'PARTIAL_SUCCESS' | 'WAITING_FOR_CONFIGURATION' | 'FAILED'
  stage: string
  questionCount: number
  answeredCount: number
  missingCount: number
  model: string | null
  errorCode: string | null
  errorMessage: string | null
  createdAt: string
  updatedAt: string
  startedAt: string | null
  finishedAt: string | null
}

export type ReviewStatus = 'PENDING' | 'CONFIRMED' | 'REJECTED'

export interface AnswerSourceSpan {
  sourceChunkId: string
  blockOrdinal: number
  start: number
  end: number
  quote: string
}

export interface ExtractionReviewItem {
  occurrenceId: string
  questionId: string
  questionVersion: number
  prompt: string
  answer: {
    content: string
    sourceType: 'USER_WRITTEN' | 'SOURCE_EXTRACTED'
    version: number
  } | null
  sourceChunkId: string | null
  sourceBlockOrdinal: number | null
  sourceLocator: Record<string, unknown>
  questionStart: number
  questionEnd: number
  questionQuote: string
  answerSpans: AnswerSourceSpan[]
  confidence: number
  reviewStatus: ReviewStatus
  createdAt: string
}

export interface SuspectedMiss {
  sourceChunkId: string
  sourceBlockOrdinal: number
  sourceLocator: Record<string, unknown>
  start: number
  end: number
  quote: string
  reason: 'QUESTION_MARK' | 'HEADING' | 'QUESTION_PATTERN'
}

export interface ExtractionReviewPage {
  items: ExtractionReviewItem[]
  page: number
  pageSize: number
  total: number
  pendingCount: number
  confirmedCount: number
  rejectedCount: number
  suspectedMisses: SuspectedMiss[]
}

export type ProfileExtractionStatus =
  | 'EMPTY'
  | 'IDLE'
  | 'PROCESSING'
  | 'SUCCEEDED'
  | 'PARTIAL_SUCCESS'
  | 'WAITING_FOR_CONFIGURATION'
  | 'FAILED'

export type SkillProficiency = 'UNSPECIFIED' | 'BEGINNER' | 'INTERMEDIATE' | 'ADVANCED'
export type SkillReviewStatus = 'PENDING' | 'CONFIRMED' | 'REJECTED'
export type PreferenceRuleType = 'INCLUDE' | 'EXCLUDE'
export type PreferenceScope = 'QUESTION' | 'ANSWER' | 'BOTH'

export interface ProfileSkill {
  id: string
  displayName: string
  canonicalName: string
  category: string
  proficiency: SkillProficiency
  evidence: string | null
  reviewStatus: SkillReviewStatus
  source: 'MODEL' | 'USER'
}

export interface UserProfile {
  id: string
  version: number
  currentVersion: number
  extractionStatus: ProfileExtractionStatus
  targetRole: string | null
  model: string | null
  errorCode: string | null
  errorMessage: string | null
  resume: AssetSummary | null
  skills: ProfileSkill[]
  createdAt: string
  updatedAt: string
}

export interface Technology {
  canonicalName: string
  displayName: string
  category: string
  aliases: string[]
  parent: string | null
}

export interface PreferenceRule {
  id: string
  ruleType: PreferenceRuleType
  technology: string
  canonicalTechnology: string
  category: string
  scope: PreferenceScope
  isActive: boolean
  version: number
  createdAt: string
  updatedAt: string
}

export interface PreferenceRulePage {
  items: PreferenceRule[]
}

export type RecommendationDecision = 'INCLUDED' | 'EXCLUDED'
export type UsableAnswerState = 'ANSWERED' | 'MISSING'

export interface RecommendationTechnology {
  canonicalName: string
  displayName: string
  category: string
}

export interface RecommendationRun {
  id: string
  status: 'SUCCEEDED'
  algorithmVersion: string
  profileVersion: number
  profileSnapshot: {
    targetRole: string | null
    skills: Array<{
      canonicalName: string
      displayName: string
      category: string
      reviewStatus: string
    }>
  }
  ruleSnapshot: Array<{
    id: string
    ruleType: PreferenceRuleType
    technology: string
    canonicalTechnology: string
    category: string
    scope: PreferenceScope
    version: number
  }>
  isCurrent: boolean
  totalCount: number
  includedCount: number
  excludedCount: number
  answeredCount: number
  missingCount: number
  createdAt: string
}

export interface RecommendationItem {
  id: string
  questionId: string | null
  collectionId: string | null
  position: number
  questionVersion: number
  answerVersion: number | null
  prompt: string
  answerContent: string | null
  answerSourceType: string | null
  decision: RecommendationDecision
  score: number
  answerState: 'MISSING' | 'SOURCE_ANSWER'
  usableAnswerState: UsableAnswerState
  questionTechnologies: RecommendationTechnology[]
  answerTechnologies: RecommendationTechnology[]
  reasonCodes: string[]
  reasons: string[]
}

export interface RecommendationItemPage {
  items: RecommendationItem[]
  page: number
  pageSize: number
  total: number
  includedCount: number
  excludedCount: number
  answeredCount: number
  missingCount: number
}

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

export const createQuestionExtractionRun = (assetId: string) =>
  apiRequest<QuestionExtractionRun>(`/api/v1/assets/${assetId}/question-extraction-runs`, {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  })

export const fetchQuestionExtractionRun = (runId: string) =>
  apiRequest<QuestionExtractionRun>(`/api/v1/question-extraction-runs/${runId}`)

export const fetchExtractionReview = (
  runId: string,
  input: { reviewStatus?: ReviewStatus; page?: number; pageSize?: number },
) => {
  const params = new URLSearchParams()
  if (input.reviewStatus) params.set('reviewStatus', input.reviewStatus)
  params.set('page', String(input.page ?? 1))
  params.set('pageSize', String(input.pageSize ?? 50))
  return apiRequest<ExtractionReviewPage>(
    `/api/v1/question-extraction-runs/${runId}/review-items?${params.toString()}`,
  )
}

export const reviewExtractionItem = (
  occurrenceId: string,
  input: {
    action: 'CONFIRM' | 'REJECT' | 'RESET'
    expectedStatus: ReviewStatus
  },
) =>
  apiRequest<ExtractionReviewItem>(
    `/api/v1/question-extraction-items/${occurrenceId}/review`,
    jsonRequest('PUT', input),
  )

export const createMissedQuestion = (
  runId: string,
  input: {
    sourceChunkId: string
    start: number
    end: number
    quote: string
    prompt: string
  },
) =>
  apiRequest<ExtractionReviewItem>(
    `/api/v1/question-extraction-runs/${runId}/missed-items`,
    jsonRequest('POST', input),
  )

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

export const fetchProfile = () => apiRequest<UserProfile>('/api/v1/profiles/me')

export const uploadResume = (file: File) => {
  const form = new FormData()
  form.append('file', file)
  return apiRequest<UserProfile>('/api/v1/profiles/me/resume', {
    method: 'POST',
    headers: { 'Idempotency-Key': crypto.randomUUID() },
    body: form,
  })
}

export const startProfileExtraction = () =>
  apiRequest<UserProfile>('/api/v1/profiles/me/extraction', { method: 'POST' })

export const updateProfileTargetRole = (input: {
  targetRole: string | null
  expectedVersion: number
}) => apiRequest<UserProfile>('/api/v1/profiles/me/target-role', jsonRequest('PUT', input))

export const createProfileSkill = (input: {
  displayName: string
  proficiency: SkillProficiency
  expectedVersion: number
}) => apiRequest<UserProfile>('/api/v1/profiles/me/skills', jsonRequest('POST', input))

export const updateProfileSkill = (
  skillId: string,
  input: {
    displayName: string
    proficiency: SkillProficiency
    reviewStatus: SkillReviewStatus
    expectedVersion: number
  },
) => apiRequest<UserProfile>(`/api/v1/profiles/me/skills/${skillId}`, jsonRequest('PUT', input))

export const deleteProfileSkill = (skillId: string, expectedVersion: number) =>
  apiRequest<UserProfile>(
    `/api/v1/profiles/me/skills/${skillId}?expectedVersion=${expectedVersion}`,
    { method: 'DELETE' },
  )

export const fetchTechnologies = () => apiRequest<Technology[]>('/api/v1/technologies')

export const fetchPreferenceRules = () =>
  apiRequest<PreferenceRulePage>('/api/v1/preference-rules')

export const createPreferenceRule = (input: {
  ruleType: PreferenceRuleType
  technology: string
  scope: PreferenceScope
}) => apiRequest<PreferenceRule>('/api/v1/preference-rules', jsonRequest('POST', input))

export const updatePreferenceRule = (
  ruleId: string,
  input: {
    ruleType: PreferenceRuleType
    technology: string
    scope: PreferenceScope
    isActive: boolean
    expectedVersion: number
  },
) => apiRequest<PreferenceRule>(`/api/v1/preference-rules/${ruleId}`, jsonRequest('PUT', input))

export const deletePreferenceRule = (ruleId: string, expectedVersion: number) =>
  apiRequest<void>(
    `/api/v1/preference-rules/${ruleId}?expectedVersion=${expectedVersion}`,
    { method: 'DELETE' },
  )

export const fetchLatestRecommendationRun = () =>
  apiRequest<RecommendationRun | null>('/api/v1/recommendation-runs/latest')

export const createRecommendationRun = () =>
  apiRequest<RecommendationRun>('/api/v1/recommendation-runs', idempotentCreate({}))

export const fetchRecommendationItems = (
  runId: string,
  input: {
    decision?: 'ANY' | RecommendationDecision
    usableAnswerState?: 'ANY' | UsableAnswerState
    q?: string
    page?: number
    pageSize?: number
  },
) => {
  const params = new URLSearchParams()
  if (input.decision) params.set('decision', input.decision)
  if (input.usableAnswerState) params.set('usableAnswerState', input.usableAnswerState)
  if (input.q) params.set('q', input.q)
  params.set('page', String(input.page ?? 1))
  params.set('pageSize', String(input.pageSize ?? 20))
  return apiRequest<RecommendationItemPage>(
    `/api/v1/recommendation-runs/${runId}/items?${params.toString()}`,
  )
}
