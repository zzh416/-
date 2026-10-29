import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Alert, Button, Card, Empty, Form, Input, Modal, Popconfirm, Select, Space, Tag, Typography } from 'antd'
import { useState } from 'react'

import { createProfileProject, deleteProfileProject, updateProfileProject, type ProfileProject, type SkillReviewStatus, type UserProfile } from '../api/client'

interface Props {
  profile: UserProfile
  disabled: boolean
  notify: (kind: 'success' | 'error', text: string) => void
}

interface ProjectForm {
  name: string
  description: string
  reviewStatus: SkillReviewStatus
}

const statuses = { CONFIRMED: '已确认', PENDING: '待确认', REJECTED: '已排除' }

export function ProfileProjects({ profile, disabled, notify }: Props) {
  const queryClient = useQueryClient()
  const [form] = Form.useForm<ProjectForm>()
  const [editing, setEditing] = useState<ProfileProject | 'new' | null>(null)
  const [editVersion, setEditVersion] = useState(profile.version)
  const onSaved = (next: UserProfile) => {
    queryClient.setQueryData(['profile'], next)
    setEditing(null)
    notify('success', '项目已保存，重新生成清单后应用关联')
  }
  const onError = (error: Error) => {
    notify('error', error.message)
    void queryClient.invalidateQueries({ queryKey: ['profile'] })
  }
  const save = useMutation({
    mutationFn: (value: ProjectForm) => {
      const input = { ...value, expectedVersion: editVersion }
      return editing && editing !== 'new' ? updateProfileProject(editing.id, input) : createProfileProject(input)
    },
    onSuccess: onSaved,
    onError,
  })
  const review = useMutation({
    mutationFn: ({ project, status }: { project: ProfileProject; status: SkillReviewStatus }) =>
      updateProfileProject(project.id, { name: project.name, description: project.description, reviewStatus: status, expectedVersion: profile.version }),
    onSuccess: onSaved,
    onError,
  })
  const remove = useMutation({
    mutationFn: (id: string) => deleteProfileProject(id, profile.version),
    onSuccess: onSaved,
    onError,
  })
  const busy = disabled || save.isPending || review.isPending || remove.isPending
  const edit = (project: ProfileProject | 'new') => {
    form.resetFields()
    form.setFieldsValue(project === 'new' ? { name: '', description: '', reviewStatus: 'CONFIRMED' } : project)
    setEditVersion(profile.version)
    setEditing(project)
  }

  return (
    <section aria-labelledby="projects-heading">
      <div className="profile-section-heading">
        <Typography.Title id="projects-heading" level={4}>4. 我的项目</Typography.Title>
        <Button disabled={busy || profile.projects.length >= 20} onClick={() => edit('new')}>添加项目</Button>
      </div>
      <Alert
        type="info" showIcon title="已确认项目用于题目关联建议"
        description="填写项目名称及实际使用的技术或方案。关联会在原技能和偏好筛选之后进行，未匹配的题目仍可通用复习。旧简历可直接补充项目。"
      />
      {profile.projects.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无项目，可手动添加；下次 AI 提取画像也会提取有原文依据的项目。" />
      ) : (
        <Space orientation="vertical" className="profile-stack">
          {profile.projects.map((project) => (
            <Card key={project.id} size="small" className="profile-card">
              <Space wrap>
                <Typography.Text strong>{project.name}</Typography.Text>
                <Tag color={project.reviewStatus === 'CONFIRMED' ? 'success' : 'default'}>{statuses[project.reviewStatus]}</Tag>
                <Tag>{project.source === 'MODEL' ? '简历原文提取' : '人工填写'}</Tag>
              </Space>
              <Typography.Paragraph style={{ whiteSpace: 'pre-wrap' }}>{project.description}</Typography.Paragraph>
              <Space wrap>
                {project.reviewStatus !== 'CONFIRMED' && <Button size="small" disabled={busy} onClick={() => review.mutate({ project, status: 'CONFIRMED' })}>确认项目</Button>}
                {project.reviewStatus !== 'REJECTED' && <Button size="small" disabled={busy} onClick={() => review.mutate({ project, status: 'REJECTED' })}>排除项目</Button>}
                <Button size="small" disabled={busy} onClick={() => edit(project)}>编辑项目</Button>
                <Popconfirm title="从当前画像删除此项目？" description="历史画像及清单保留原记录。" onConfirm={() => remove.mutate(project.id)}>
                  <Button size="small" danger disabled={busy}>删除项目</Button>
                </Popconfirm>
              </Space>
            </Card>
          ))}
        </Space>
      )}
      <Modal title={editing === 'new' ? '添加项目' : '编辑项目'} open={editing !== null} onCancel={() => setEditing(null)}
        onOk={() => form.submit()} okText="保存项目" cancelText="取消" confirmLoading={save.isPending}
        cancelButtonProps={{ disabled: save.isPending }} closable={!save.isPending} mask={{ closable: !save.isPending }} keyboard={!save.isPending}>
        <Form form={form} layout="vertical" onFinish={(value) => save.mutate(value)}>
          <Form.Item name="name" label="项目名称" rules={[{ required: true, whitespace: true, message: '请输入项目名称' }]}>
            <Input maxLength={120} disabled={save.isPending} />
          </Form.Item>
          <Form.Item name="description" label="项目说明与技术" rules={[{ required: true, whitespace: true, message: '请填写项目说明及所用技术' }]}>
            <Input.TextArea rows={5} maxLength={4000} showCount disabled={save.isPending} placeholder="例如：使用 Python、LangGraph 和 RAG 构建知识库助手，包含向量检索与重排。" />
          </Form.Item>
          <Form.Item name="reviewStatus" label="确认状态">
            <Select disabled={save.isPending} options={Object.entries(statuses).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          <Typography.Text type="secondary">保存后请重新生成复习清单。项目关联不会添加技能或覆盖排除偏好。</Typography.Text>
        </Form>
      </Modal>
    </section>
  )
}
