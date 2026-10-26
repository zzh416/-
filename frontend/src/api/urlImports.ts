import { apiRequest, jsonRequest, type AssetSummary } from './client'

export type UrlImportDisposition = 'CREATED' | 'REUSED' | 'DUPLICATE' | 'INVALID'

export interface UrlImportItem {
  ordinal: number
  inputUrl: string
  normalizedUrl: string | null
  disposition: UrlImportDisposition
  message: string | null
  duplicateOf: number | null
  asset: AssetSummary | null
}

export interface UrlImportBatch {
  id: string
  collectionId: string
  createdAt: string
  items: UrlImportItem[]
}

export interface UrlImportPage {
  items: UrlImportBatch[]
}

const collectionPath = (collectionId: string) =>
  `/api/v1/collections/${encodeURIComponent(collectionId)}/url-imports`

export const createUrlImport = (
  collectionId: string,
  input: { urls: string[] },
  idempotencyKey: string,
) => {
  const request = jsonRequest('POST', input)
  const headers = new Headers(request.headers)
  headers.set('Idempotency-Key', idempotencyKey)
  return apiRequest<UrlImportBatch>(collectionPath(collectionId), { ...request, headers })
}

export const fetchUrlImports = (collectionId: string) =>
  apiRequest<UrlImportPage>(collectionPath(collectionId))

export const fetchUrlImport = (batchId: string) =>
  apiRequest<UrlImportBatch>(`/api/v1/url-imports/${encodeURIComponent(batchId)}`)

export const dispatchUrlImport = (batchId: string) =>
  apiRequest<UrlImportBatch>(`/api/v1/url-imports/${encodeURIComponent(batchId)}/dispatch`, {
    method: 'POST',
  })
