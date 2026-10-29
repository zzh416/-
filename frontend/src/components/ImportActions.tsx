import { Upload, type UploadProps } from 'antd'
import { WorkspaceIcon, type WorkspaceIconName } from './WorkspaceIcon'

interface Props {
  beforeUpload: UploadProps['beforeUpload']
  uploading: boolean
  onUrl: () => void
  onBatch: () => void
  onText: () => void
}

function ActionLabel({ icon, title, detail }: { icon: WorkspaceIconName; title: string; detail: string }) {
  return <>
    <span className="import-action-icon"><WorkspaceIcon name={icon} /></span>
    <span className="import-action-copy"><strong>{title}</strong><small>{detail}</small></span>
    <span className="import-action-arrow"><WorkspaceIcon name="arrow" size={16} /></span>
  </>
}

export function ImportActions({ beforeUpload, uploading, onUrl, onBatch, onText }: Props) {
  return <div className="import-actions" aria-label="选择资料导入方式">
    <Upload accept=".txt,.md,.markdown,.png,.jpg,.jpeg,.pdf,.docx,.doc" showUploadList={false}
      beforeUpload={beforeUpload} disabled={uploading}>
      <button className="import-action import-action-file" type="button" disabled={uploading} aria-busy={uploading}>
        <ActionLabel icon="file" title={uploading ? '正在上传…' : '导入文件'} detail="图片 / PDF / Word / 文本" />
      </button>
    </Upload>
    <button className="import-action import-action-link" type="button" onClick={onUrl}>
      <ActionLabel icon="link" title="导入网页链接" detail="收藏一篇值得读的面经" />
    </button>
    <button className="import-action import-action-batch" type="button" onClick={onBatch}>
      <ActionLabel icon="batch" title="批量导入链接" detail="一次整理多篇网页资料" />
    </button>
    <button className="import-action import-action-text" type="button" onClick={onText}>
      <ActionLabel icon="text" title="粘贴文字" detail="随手记下，留待慢慢消化" />
    </button>
  </div>
}
