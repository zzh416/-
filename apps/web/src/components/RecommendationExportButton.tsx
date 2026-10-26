import { useMutation } from '@tanstack/react-query'
import { Alert, Button, Checkbox, Modal, Typography } from 'antd'
import { useState } from 'react'

import { fetchRecommendationExport, type RecommendationRun } from '../api/client'

interface Props {
  run: RecommendationRun
  notify: (kind: 'success' | 'error', text: string) => void
}

export function RecommendationExportButton({ run, notify }: Props) {
  const [open, setOpen] = useState(false)
  const [includeNotes, setIncludeNotes] = useState(false)
  const download = useMutation({
    mutationFn: () => fetchRecommendationExport(run.id, includeNotes),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `面试备考包-${run.id.slice(0, 8)}.md`
      document.body.append(anchor)
      anchor.click()
      anchor.remove()
      // Allow the browser to consume the URL before releasing the download buffer.
      window.setTimeout(() => URL.revokeObjectURL(url), 60_000)
      setOpen(false)
      notify('success', '备考包已开始下载')
    },
    onError: (error: Error) => notify('error', error.message),
  })

  return (
    <>
      <Button
        disabled={run.includedCount === 0}
        title={run.includedCount === 0 ? '这份清单没有可导出的题目' : undefined}
        onClick={() => { setIncludeNotes(false); setOpen(true) }}
      >
        导出 Markdown
      </Button>
      <Modal
        title="导出个人备考包"
        open={open}
        okText="下载 Markdown"
        cancelText="取消"
        confirmLoading={download.isPending}
        cancelButtonProps={{ disabled: download.isPending }}
        closable={!download.isPending}
        maskClosable={!download.isPending}
        keyboard={!download.isPending}
        onCancel={() => setOpen(false)}
        onOk={() => download.mutate()}
      >
        <Typography.Paragraph>
          导出全部 {run.includedCount} 道“进入清单”的题目，覆盖全部分页，不受当前搜索、项目和答案筛选影响。
          包含已有的适用答案及保存的来源信息。已确认的 AI 补充会保留 AI 来源标注；未确认的草稿不作为答案导出。
        </Typography.Paragraph>
        {!run.isCurrent && (
          <Alert
            type="warning"
            showIcon
            title="将导出历史清单"
            description="文件会标明历史版本，并沿用保存时的题目、答案和来源。"
          />
        )}
        <Typography.Paragraph type="secondary">
          不包含已排除题目和不适用答案。导出不调用 AI。
        </Typography.Paragraph>
        <Checkbox checked={includeNotes} disabled={download.isPending} onChange={(event) => setIncludeNotes(event.target.checked)}>
          包含导出时的个人备注
        </Checkbox>
      </Modal>
    </>
  )
}
