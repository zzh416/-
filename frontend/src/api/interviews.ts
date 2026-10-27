import { apiRequest, idempotentCreate, jsonRequest, type SourceStatistics } from './client'
import type { AnswerSourceType } from './aiAnswerTypes'

export type QuestionSource = 'BANK' | 'AI' | 'HYBRID'

export interface InterviewQuestion {
  id: string
  prompt: string | null
  kind: 'BANK' | 'AI' | 'AI_FOLLOWUP'
  topics: string[]
  reason: string
  projectName: string | null
  projectQuote: string | null
  skillBasis: string | null
  sourceStatistics: SourceStatistics | null
  referenceAnswerSourceType: AnswerSourceType | null
}

export interface InterviewTurn extends InterviewQuestion {
  rootId: string
  depth: number
  draft: string
  answer: string
  answerStatus: 'DRAFT' | 'ANSWERED' | 'DONT_KNOW' | 'SKIPPED'
  deadlineAt: string | null
  startedAt: string
  allocatedSeconds: number
  remainingSeconds: number
  elapsedSeconds: number
  timedOut: boolean
  feedback: { summary: string; coveredPoints: string[]; gaps: string[] } | null
  followup: { prompt: string; answerQuote: string; projectQuote: string | null; questionQuote: string | null } | null
  reviewError: string | null
  reviewStatus: string
  reviewAttempts: number
  answerQuote: string | null
  questionQuote: string | null
}

export interface InterviewSession {
  id: string
  version: number
  status: 'PLANNING' | 'PLAN_FAILED' | 'READY' | 'ACTIVE' | 'PAUSED' | 'REVIEWING' | 'AWAITING_NEXT' | 'COMPLETED'
  serverNow: string
  isCurrent: boolean
  mode: 'MOCK' | 'PREVIEW'
  questionSource: QuestionSource
  questionCount: number
  questionSeconds: number
  plan: InterviewQuestion[]
  turns: InterviewTurn[]
  blindTopics: string[]
  skippedPlanCount: number
  notice: string
  finishedReason: string | null
  planningError: string | null
  model: string | null
  jobStage: string
  canRetry: boolean
  planAttempts: number
  planningDiagnostics: {
    targetCount: number
    acceptedCount: number
    modelCalls: number
    returnedCount: number
    formatIssueCount: number
    rejectionCounts: Record<string, number>
    errors: string[]
    schemaPaths: string[]
    exhausted: boolean
  } | null
  createdAt: string
}

export interface InterviewCreate {
  recommendationRunId?: string
  questionSource: QuestionSource
  projectId?: string
  questionCount: number
  questionSeconds: number
  mode: 'MOCK' | 'PREVIEW'
}

export const createInterview = (input: InterviewCreate) => apiRequest<InterviewSession>('/api/v1/interview-sessions', idempotentCreate(input))
export const fetchInterviewHistory = () => apiRequest<{ items: Array<{ id: string; status: InterviewSession['status']; createdAt: string }> }>('/api/v1/interview-sessions')
export const fetchInterview = (id: string) => apiRequest<InterviewSession>(`/api/v1/interview-sessions/${id}`)
export const interviewAction = (id: string, input: { expectedVersion: number; action: string; turnId?: string; seconds?: number }) =>
  apiRequest<InterviewSession>(`/api/v1/interview-sessions/${id}/action`, jsonRequest('POST', input))
export const saveInterviewDraft = (id: string, input: { expectedVersion: number; turnId: string; text: string }) =>
  apiRequest<InterviewSession>(`/api/v1/interview-sessions/${id}/draft`, jsonRequest('PUT', input))
export const submitInterviewAnswer = (id: string, input: { expectedVersion: number; turnId: string; text: string; action: 'ANSWER' | 'DONT_KNOW' | 'SKIP' }) =>
  apiRequest<InterviewSession>(`/api/v1/interview-sessions/${id}/answer`, jsonRequest('POST', input))
