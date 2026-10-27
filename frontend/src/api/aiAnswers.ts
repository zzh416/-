import { apiRequest, idempotentCreate, jsonRequest } from './client'
import type { AIAnswerRun } from './aiAnswerTypes'

export type { AIAnswerRun, AnswerSourceType, EffectiveAnswer } from './aiAnswerTypes'

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
