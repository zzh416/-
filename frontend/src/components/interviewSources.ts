import type { QuestionSource } from '../api/interviews'

export const sourceLabels: Record<QuestionSource, string> = {
  BANK: '题库出题', AI: 'AI 自主出题', HYBRID: '混合出题',
}
