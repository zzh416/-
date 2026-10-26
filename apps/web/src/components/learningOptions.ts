import type { LearningStatus } from '../api/learning'

export const learningOptions: { value: LearningStatus; label: string }[] = [
  { value: 'UNMARKED', label: '未标记' }, { value: 'MASTERED', label: '已掌握' },
  { value: 'WEAK', label: '不熟悉' }, { value: 'UNKNOWN', label: '不会' },
]
