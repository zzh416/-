import type { QueryClient } from '@tanstack/react-query'

export const invalidatePersonalizedContent = (client: QueryClient) => Promise.all(
  ['questions', 'question', 'ai-answer', 'ai-answer-run', 'learning', 'recommendation-run', 'recommendation-items']
    .map((key) => client.invalidateQueries({ queryKey: [key] })),
)
