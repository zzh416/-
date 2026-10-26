import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Button, Input, Space, Typography } from 'antd'
import { useId, useState } from 'react'

import { updateAssetSourceMetadata, type AssetSummary } from '../api/client'

export function AssetSourceMetadata({ asset, notify }: { asset: AssetSummary; notify: (kind: 'success' | 'error', text: string) => void }) {
  const inputId = useId()
  const queryClient = useQueryClient()
  const [sourceDate, setSourceDate] = useState(asset.sourceDate ?? '')
  const save = useMutation({
    mutationFn: () => updateAssetSourceMetadata(asset.id, { sourceDate: sourceDate || null, expectedVersion: asset.sourceMetadataVersion }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['asset', asset.id] }),
        queryClient.invalidateQueries({ queryKey: ['assets'] }),
        queryClient.invalidateQueries({ queryKey: ['questions'] }),
        queryClient.invalidateQueries({ queryKey: ['recommendation-run'] }),
      ])
      notify('success', '资料日期已保存，来源统计已更新')
    },
    onError: (error: Error) => notify('error', error.message),
  })
  return (
    <div className="asset-source-metadata">
      <Space wrap align="center">
        <label htmlFor={inputId}>资料日期</label>
        <Input id={inputId} type="date" value={sourceDate} onChange={(event) => setSourceDate(event.target.value)} />
        <Button size="small" loading={save.isPending} disabled={sourceDate === (asset.sourceDate ?? '')} onClick={() => save.mutate()}>保存日期</Button>
        {sourceDate && <Button size="small" onClick={() => setSourceDate('')}>清空</Button>}
      </Space>
      <Typography.Paragraph type="secondary">填写你核对过的面经发生日期或原文发布日期；未知时留空，抓取时间不会用于时效统计。</Typography.Paragraph>
    </div>
  )
}
