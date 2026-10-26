import { apiRequest, idempotentCreate, jsonRequest } from './client'

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

export const createAIAnswer = (questionId: string, input: {
  expectedQuestionVersion: number
  useProfile: boolean
  includeNote: boolean
}) => apiRequest<AIAnswerRun>(`/api/v1/questions/${questionId}/ai-answer-runs`, idempotentCreate(input))

export const fetchLatestAIAnswer = (questionId: string) =>
  apiRequest<AIAnswerRun | null>(`/api/v1/questions/${questionId}/ai-answer-runs/latest`)

export const fetchAIAnswer = (runId: string) =>
  apiRequest<AIAnswerRun>(`/api/v1/ai-answer-runs/${runId}`)

export const retryAIAnswer = (run: AIAnswerRun) =>
  apiRequest<AIAnswerRun>(`/api/v1/ai-answer-runs/${run.id}/retry`, jsonRequest('POST', { expectedVersion: run.version }))

export const reviewAIAnswer = (run: AIAnswerRun, action: 'SAVE_DRAFT' | 'CONFIRM' | 'DISCARD', content?: string) =>
  apiRequest<AIAnswerRun>(`/api/v1/ai-answer-runs/${run.id}`, jsonRequest('PUT', {
    expectedVersion: run.version, action, ...(content === undefined ? {} : { content }),
  }))
