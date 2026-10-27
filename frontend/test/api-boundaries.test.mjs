import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

test('the base API client does not import feature API modules', async () => {
  const clientSource = await readFile(new URL('../src/api/client.ts', import.meta.url), 'utf8')

  assert.doesNotMatch(
    clientSource,
    /from\s+['"]\.\/aiAnswers['"]/,
    'Move shared answer contracts out of aiAnswers.ts before using them in client.ts',
  )
})
