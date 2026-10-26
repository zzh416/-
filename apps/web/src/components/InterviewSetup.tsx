import { Alert, Button, Card, Form, InputNumber, Radio, Select, Space } from 'antd'
import type { RecommendationRun, UserProfile } from '../api/client'
import type { InterviewCreate, QuestionSource } from '../api/interviews'
import { sourceLabels } from './interviewSources'

interface Props {
  run: RecommendationRun | null | undefined
  profile: UserProfile | undefined
  pending: boolean
  onSubmit: (input: InterviewCreate) => void
  onBack?: () => void
  onOpenProfile: () => void
}

export function InterviewSetup({ run, profile, pending, onSubmit, onBack, onOpenProfile }: Props) {
  const [form] = Form.useForm<InterviewCreate>()
  const initialSource: QuestionSource = run?.isCurrent && run.includedCount > 0 ? 'HYBRID' : 'AI'
  const source = Form.useWatch('questionSource', form) ?? initialSource
  const hasProfile = Boolean(profile?.skills.some((skill) => skill.reviewStatus === 'CONFIRMED') || profile?.projects.some((project) => project.reviewStatus === 'CONFIRMED'))
  const canStart = source === 'AI' ? hasProfile : Boolean(run?.isCurrent)
  const projects = (source === 'AI' ? profile?.projects : run?.profileSnapshot.projects)?.filter((project) => project.reviewStatus === 'CONFIRMED') ?? []
  return <Card title="本轮设置">
    <Form<InterviewCreate> form={form} layout="vertical" initialValues={{ questionSource: initialSource, questionCount: 8, questionSeconds: 180, mode: 'MOCK' }}
      onValuesChange={(changed) => { if (changed.questionSource) form.setFieldValue('projectId', undefined) }}
      onFinish={(values) => onSubmit({ ...values, recommendationRunId: source === 'AI' ? undefined : run!.id })}>
      <Form.Item name="questionSource" label="出题来源" rules={[{ required: true }]}>
        <Radio.Group options={(['BANK', 'AI', 'HYBRID'] as const).map((value) => ({ value, label: sourceLabels[value] }))} />
      </Form.Item>
      <Alert type="info" showIcon title={source === 'AI' ? '没有题库也能开始' : source === 'BANK' ? '基础题全部来自你的题库' : '题库优先，AI补足'}
        description={source === 'AI' ? '根据已确认技能和项目新出题，不读取题库；每题标明技能或项目依据。不生成参考答案。'
          : source === 'BANK' ? '只从当前复习清单选题，不调用AI出基础题。反馈和你选择的追问仍可使用AI；题量不足时不会自动补题。'
            : '优先选择当前清单中的题，数量不足时再按已确认技能或项目补充AI题，分别标明来源。'} />
      {source === 'AI' && !hasProfile && <Alert type="warning" showIcon title="请先确认技能或项目" description="可以上传简历并确认提取结果，或手动添加技能、项目；不需要先建知识库。" action={<Button onClick={onOpenProfile}>完善我的画像</Button>} />}
      {source !== 'AI' && !run?.isCurrent && <Alert type="warning" showIcon title="请先按当前画像和偏好生成复习清单，或选择AI自主出题。" />}
      {source !== 'AI' && run?.isCurrent && run.includedCount === 0 && <Alert type="warning" title="当前清单没有题库题。可选AI自主出题；混合模式需要已确认技能或项目才能补题。" />}
      <Form.Item name="projectId" label="面试范围" extra="综合会结合技能和项目；选择项目后集中问该项目。没有项目也能按技能自主出题。">
        <Select allowClear placeholder="综合：技能基础与项目" options={projects.map((project) => ({ value: project.id, label: project.name }))} />
      </Form.Item>
      <Form.Item name="questionCount" label="基础题数量"><InputNumber aria-label="基础题数量" min={3} max={12} /></Form.Item>
      <Form.Item name="questionSeconds" label="每题作答秒数" extra="打字默认180秒，支持60–900秒，作答中可加时。"><InputNumber aria-label="每题作答秒数" min={60} max={900} step={30} /></Form.Item>
      <Form.Item name="mode" label="训练模式"><Radio.Group options={[{ value: 'MOCK', label: '逐题模拟（先不看题）' }, { value: 'PREVIEW', label: '备考预览（先看清单）' }]} /></Form.Item>
      <Space wrap><Button htmlType="submit" type="primary" loading={pending} disabled={!canStart}>生成本轮面试计划</Button>{onBack && <Button onClick={onBack}>返回记录</Button>}</Space>
    </Form>
  </Card>
}
