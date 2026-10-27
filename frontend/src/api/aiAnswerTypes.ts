export type AnswerSourceType = 'USER_WRITTEN' | 'SOURCE_EXTRACTED' | 'AI_GENERATED'

export interface AIAnswerRun {
  id: string
  questionId: string
  questionVersion: number
  version: number
  status: 'QUEUED' | 'PROCESSING' | 'SUCCEEDED' | 'FAILED'
  reviewStatus: 'DRAFT' | 'CONFIRMED' | 'DISCARDED'
  content: string | null
  sourceType: 'AI_GENERATED'
  basis: 'GENERAL_KNOWLEDGE' | 'SOURCE_ASSISTED' | null
  citations: Array<{ chunkId: string; quote: string }>
  provider: string | null
  model: string | null
  usedProfile: boolean
  usedNote: boolean
  isCurrent: boolean
  retryCount: number
  retryLimit: number
  canRetry: boolean
  errorCode: string | null
  errorMessage: string | null
  createdAt: string
  completedAt: string | null
}

export interface EffectiveAnswer {
  content: string
  sourceType: AnswerSourceType
  version: number
  aiAnswerId: string | null
}
