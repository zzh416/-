import { apiRequest, jsonRequest } from './client'
import type { AnswerSourceType } from './aiAnswerTypes'

export type LearningStatus = 'UNMARKED' | 'MASTERED' | 'WEAK' | 'UNKNOWN'
export type LearningFilter = 'TO_REVIEW' | 'ANY' | Exclude<LearningStatus, 'UNMARKED'>
export interface LearningState {
  id: string | null
  questionId: string | null
  sourceKind: 'BANK' | 'INTERVIEW'
  status: LearningStatus
  version: number
  questionVersion: number
  answerVersion: number | null
  aiAnswerId: string | null
  aiAnswerVersion: number | null
  contentChanged: boolean
  updatedAt: string | null
}
export interface LearningItem extends LearningState {
  prompt: string
  answerContent: string | null
  answerSourceType: AnswerSourceType | null
  sourceSessionId: string | null
  sourceTurnId: string | null
}
export interface LearningPage {
  items: LearningItem[]
  page: number
  pageSize: number
  total: number
  excludedCount: number
  unknownCount: number
  weakCount: number
  masteredCount: number
  changedCount: number
}
export interface LearningCandidate {
  turnId: string
  prompt: string
  sourceKind: 'BANK' | 'INTERVIEW'
  confirmed: boolean
  canConfirm: boolean
  message: string | null
  learning: LearningState | null
}
export const lookupLearning = (questionIds: string[]) => apiRequest<{ items: LearningState[] }>('/api/v1/learning/lookup', jsonRequest('POST', { questionIds }))
export const updateLearning = (item: LearningState, status: LearningStatus) => {
  const path = item.questionId ? `questions/${item.questionId}` : `items/${item.id}`
  return apiRequest<LearningState>(`/api/v1/learning/${path}`, jsonRequest('PUT', {
    status, expectedVersion: item.version, questionVersion: item.questionVersion, answerVersion: item.answerVersion,
    aiAnswerId: item.aiAnswerId, aiAnswerVersion: item.aiAnswerVersion,
  }))
}
export const fetchLearning = (state: LearningFilter, q: string, page: number) => apiRequest<LearningPage>(`/api/v1/learning/items?${new URLSearchParams({ state, q, page: String(page), pageSize: '20' })}`)
export const fetchLearningCandidates = (sessionId: string) => apiRequest<{ items: LearningCandidate[] }>(`/api/v1/learning/interviews/${sessionId}/candidates`)
export const confirmLearningCandidate = (sessionId: string, turnId: string) => apiRequest<LearningState>(`/api/v1/learning/interviews/${sessionId}/turns/${turnId}`, { method: 'POST' })
