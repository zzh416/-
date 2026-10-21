import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Divider,
  Drawer,
  Empty,
  Form,
  Input,
  Modal,
  Popconfirm,
  Radio,
  Select,
  Skeleton,
  Space,
  Switch,
  Tag,
  Typography,
  Upload,
} from 'antd'
import { useMemo, useState } from 'react'
import {
  createPreferenceRule,
  createProfileSkill,
  deletePreferenceRule,
  deleteProfileSkill,
  fetchPreferenceRules,
  fetchProfile,
  fetchTechnologies,
  retryAsset,
  startProfileExtraction,
  updatePreferenceRule,
  updateProfileSkill,
  updateProfileTargetRole,
  uploadResume,
  type PreferenceRuleType,
  type PreferenceScope,
  type ProfileSkill,
  type SkillProficiency,
  type SkillReviewStatus,
  type UserProfile,
} from '../api/client'

interface ProfileDrawerProps {
  open: boolean
  onClose: () => void
  notify: (kind: 'success' | 'error', text: string) => void
}

interface SkillFormValue {
  displayName: string
  proficiency: SkillProficiency
  reviewStatus: SkillReviewStatus
}

interface RuleFormValue {
  ruleType: PreferenceRuleType
  technology: string
  scope: PreferenceScope
}

const proficiencyLabels: Record<SkillProficiency, string> = {
  UNSPECIFIED: '未标注',
  BEGINNER: '入门',
  INTERMEDIATE: '熟练',
  ADVANCED: '深入',
}

const reviewLabels: Record<SkillReviewStatus, string> = {
  PENDING: '待确认',
  CONFIRMED: '已确认',
  REJECTED: '已排除',
}

const extractionLabels: Record<string, string> = {
  EMPTY: '尚未建立画像',
  IDLE: '等待提取',
  PROCESSING: '正在提取',
  SUCCEEDED: '提取完成',
  PARTIAL_SUCCESS: '部分完成',
  WAITING_FOR_CONFIGURATION: '等待 GLM 配置',
  FAILED: '提取失败',
}

const assetStatusLabels: Record<string, string> = {
  QUEUED: '等待解析',
  PROCESSING: '正在解析',
  READY: '解析完成',
  PARTIAL_SUCCESS: '部分解析完成',
  WAITING_FOR_CONFIGURATION: '等待 OCR 配置',
  FAILED: '解析失败',
}

const isProfilePending = (profile?: UserProfile) =>
  profile?.extractionStatus === 'PROCESSING' ||
  profile?.resume?.status === 'QUEUED' ||
  profile?.resume?.status === 'PROCESSING'

function TargetRoleEditor({
  value,
  disabled,
  loading,
  onSave,
}: {
  value: string | null
  disabled: boolean
  loading: boolean
  onSave: (value: string) => void
}) {
  const [draft, setDraft] = useState(value ?? '')

  return (
    <Space.Compact className="profile-role-input">
      <Input
        value={draft}
        maxLength={200}
        disabled={disabled}
        placeholder="例如：Python 后端开发、AI Agent 工程师"
        onChange={(event) => setDraft(event.target.value)}
      />
      <Button
        type="primary"
        loading={loading}
        disabled={disabled || draft.trim() === (value ?? '')}
        onClick={() => onSave(draft)}
      >
        保存
      </Button>
    </Space.Compact>
  )
}

export function ProfileDrawer({ open, onClose, notify }: ProfileDrawerProps) {
  const queryClient = useQueryClient()
  const [skillForm] = Form.useForm<SkillFormValue>()
  const [ruleForm] = Form.useForm<RuleFormValue>()
  const [editingSkill, setEditingSkill] = useState<ProfileSkill | 'new' | null>(null)

  const profile = useQuery({
    queryKey: ['profile'],
    queryFn: fetchProfile,
    enabled: open,
    refetchInterval: (query) => (isProfilePending(query.state.data) ? 1_500 : false),
  })
  const technologies = useQuery({
    queryKey: ['technologies'],
    queryFn: fetchTechnologies,
    enabled: open,
  })
  const rules = useQuery({
    queryKey: ['preference-rules'],
    queryFn: fetchPreferenceRules,
    enabled: open,
  })

  const updateProfileCache = (next: UserProfile) => {
    queryClient.setQueryData(['profile'], next)
  }
  const reportError = (error: Error) => notify('error', error.message)

  const resumeUpload = useMutation({
    mutationFn: uploadResume,
    onSuccess: (next) => {
      updateProfileCache(next)
      notify('success', '简历已上传，正在解析文字')
    },
    onError: reportError,
  })
  const resumeRetry = useMutation({
    mutationFn: retryAsset,
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['profile'] })
      notify('success', '已重新提交简历解析')
    },
    onError: reportError,
  })
  const extraction = useMutation({
    mutationFn: startProfileExtraction,
    onSuccess: (next) => {
      updateProfileCache(next)
      notify('success', '已开始从简历提取技能画像')
    },
    onError: reportError,
  })
  const targetRoleUpdate = useMutation({
    mutationFn: (value: string) =>
      updateProfileTargetRole({
        targetRole: value.trim() || null,
        expectedVersion: profile.data!.version,
      }),
    onSuccess: (next) => {
      updateProfileCache(next)
      notify('success', '目标岗位已保存')
    },
    onError: reportError,
  })
  const skillSave = useMutation({
    mutationFn: (value: SkillFormValue) =>
      editingSkill === 'new'
        ? createProfileSkill({
            displayName: value.displayName,
            proficiency: value.proficiency,
            expectedVersion: profile.data!.version,
          })
        : updateProfileSkill(editingSkill!.id, {
            ...value,
            expectedVersion: profile.data!.version,
          }),
    onSuccess: (next) => {
      updateProfileCache(next)
      setEditingSkill(null)
      notify('success', '技能已保存')
    },
    onError: reportError,
  })
  const skillDelete = useMutation({
    mutationFn: (skillId: string) => deleteProfileSkill(skillId, profile.data!.version),
    onSuccess: (next) => {
      updateProfileCache(next)
      notify('success', '技能已删除')
    },
    onError: reportError,
  })
  const skillReview = useMutation({
    mutationFn: ({ skill, reviewStatus }: { skill: ProfileSkill; reviewStatus: SkillReviewStatus }) =>
      updateProfileSkill(skill.id, {
        displayName: skill.displayName,
        proficiency: skill.proficiency,
        reviewStatus,
        expectedVersion: profile.data!.version,
      }),
    onSuccess: (next) => updateProfileCache(next),
    onError: reportError,
  })
  const ruleCreate = useMutation({
    mutationFn: createPreferenceRule,
    onSuccess: () => {
      ruleForm.resetFields()
      void queryClient.invalidateQueries({ queryKey: ['preference-rules'] })
      notify('success', '筛选偏好已添加')
    },
    onError: reportError,
  })
  const ruleToggle = useMutation({
    mutationFn: ({ id, enabled }: { id: string; enabled: boolean }) => {
      const rule = rules.data!.items.find((item) => item.id === id)!
      return updatePreferenceRule(id, {
        ruleType: rule.ruleType,
        technology: rule.technology,
        scope: rule.scope,
        isActive: enabled,
        expectedVersion: rule.version,
      })
    },
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ['preference-rules'] }),
    onError: reportError,
  })
  const ruleDelete = useMutation({
    mutationFn: (id: string) => {
      const rule = rules.data!.items.find((item) => item.id === id)!
      return deletePreferenceRule(id, rule.version)
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['preference-rules'] })
      notify('success', '筛选偏好已删除')
    },
    onError: reportError,
  })

  const technologyOptions = useMemo(
    () =>
      (technologies.data ?? []).map((item) => ({
        label: `${item.displayName} · ${item.category}`,
        value: item.displayName,
      })),
    [technologies.data],
  )
  const resumeReady = Boolean(
    profile.data?.resume && ['READY', 'PARTIAL_SUCCESS'].includes(profile.data.resume.status),
  )
  const profileLocked = profile.data?.extractionStatus === 'PROCESSING'

  const openSkill = (skill: ProfileSkill | 'new') => {
    setEditingSkill(skill)
    if (skill === 'new') {
      skillForm.setFieldsValue({
        displayName: '',
        proficiency: 'UNSPECIFIED',
        reviewStatus: 'CONFIRMED',
      })
    } else {
      skillForm.setFieldsValue({
        displayName: skill.displayName,
        proficiency: skill.proficiency,
        reviewStatus: skill.reviewStatus,
      })
    }
  }

  return (
    <>
      <Drawer
        classNames={{ root: 'profile-drawer' }}
        title="我的画像与筛选偏好"
        size={760}
        open={open}
        onClose={onClose}
      >
        {profile.isLoading && <Skeleton active paragraph={{ rows: 10 }} />}
        {profile.isError && (
          <Alert
            type="error"
            showIcon
            title="画像加载失败"
            description={profile.error.message}
            action={<Button onClick={() => profile.refetch()}>重试</Button>}
          />
        )}
        {profile.data && (
          <Space orientation="vertical" size="large" className="profile-stack">
            <section aria-labelledby="resume-heading">
              <div className="profile-section-heading">
                <div>
                  <Typography.Title id="resume-heading" level={4}>1. 我的简历</Typography.Title>
                  <Typography.Text type="secondary">
                    简历单独保存，不会出现在面经或知识库中。
                  </Typography.Text>
                </div>
                <Upload
                  accept=".txt,.md,.markdown,.png,.jpg,.jpeg,.pdf,.docx,.doc"
                  showUploadList={false}
                  disabled={resumeUpload.isPending || profileLocked}
                  beforeUpload={(file) => {
                    resumeUpload.mutate(file)
                    return false
                  }}
                >
                  <Button loading={resumeUpload.isPending} disabled={profileLocked}>
                    {profile.data.resume ? '更换简历' : '上传简历'}
                  </Button>
                </Upload>
              </div>
              {profile.data.resume ? (
                <Card size="small" className="profile-card">
                  <Space wrap>
                    <Typography.Text strong>{profile.data.resume.originalFilename ?? profile.data.resume.title}</Typography.Text>
                    <Tag color={resumeReady ? 'success' : profile.data.resume.status === 'FAILED' ? 'error' : 'processing'}>
                      {assetStatusLabels[profile.data.resume.status]}
                    </Tag>
                  </Space>
                  {profile.data.resume.job?.errorMessage && (
                    <Alert
                      className="profile-inline-alert"
                      type="error"
                      showIcon
                      title={profile.data.resume.job.errorMessage}
                      action={
                        <Button
                          size="small"
                          loading={resumeRetry.isPending}
                          onClick={() => resumeRetry.mutate(profile.data!.resume!.id)}
                        >
                          重试解析
                        </Button>
                      }
                    />
                  )}
                  <div className="profile-card-actions">
                    <Button
                      type="primary"
                      disabled={!resumeReady || profileLocked}
                      loading={extraction.isPending || profileLocked}
                      onClick={() => extraction.mutate()}
                    >
                      AI 提取画像
                    </Button>
                    <Typography.Text type="secondary">
                      {extractionLabels[profile.data.extractionStatus]}
                    </Typography.Text>
                  </div>
                  {profile.data.errorMessage && (
                    <Alert
                      className="profile-inline-alert"
                      type={profile.data.extractionStatus === 'PARTIAL_SUCCESS' ? 'warning' : 'error'}
                      showIcon
                      title={profile.data.errorMessage}
                    />
                  )}
                </Card>
              ) : (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="上传简历后可提取技能和目标岗位" />
              )}
            </section>

            <Divider />
            <section aria-labelledby="role-heading">
              <Typography.Title id="role-heading" level={4}>2. 目标岗位</Typography.Title>
              <TargetRoleEditor
                key={`${profile.data.currentVersion}-${profile.data.targetRole ?? ''}`}
                value={profile.data.targetRole}
                disabled={profileLocked}
                loading={targetRoleUpdate.isPending}
                onSave={(value) => targetRoleUpdate.mutate(value)}
              />
            </section>

            <Divider />
            <section aria-labelledby="skills-heading">
              <div className="profile-section-heading">
                <div>
                  <Typography.Title id="skills-heading" level={4}>3. 技能画像</Typography.Title>
                  <Typography.Text type="secondary">
                    AI 提取的技能先标记为待确认；你可以确认、排除、编辑或补充。
                  </Typography.Text>
                </div>
                <Button disabled={profileLocked} onClick={() => openSkill('new')}>添加技能</Button>
              </div>
              {profile.data.skills.length === 0 ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有技能画像" />
              ) : (
                <div className="profile-skill-list">
                  {profile.data.skills.map((skill) => (
                    <Card key={skill.id} size="small" className={skill.reviewStatus === 'REJECTED' ? 'profile-card muted' : 'profile-card'}>
                      <div className="profile-skill-heading">
                        <Space wrap>
                          <Typography.Text strong>{skill.displayName}</Typography.Text>
                          <Tag>{skill.category}</Tag>
                          <Tag>{proficiencyLabels[skill.proficiency]}</Tag>
                          <Tag color={skill.reviewStatus === 'CONFIRMED' ? 'success' : skill.reviewStatus === 'REJECTED' ? 'default' : 'warning'}>
                            {reviewLabels[skill.reviewStatus]}
                          </Tag>
                        </Space>
                        <Space wrap>
                          {skill.reviewStatus !== 'CONFIRMED' && (
                            <Button size="small" type="link" disabled={profileLocked} onClick={() => skillReview.mutate({ skill, reviewStatus: 'CONFIRMED' })}>确认</Button>
                          )}
                          {skill.reviewStatus !== 'REJECTED' && (
                            <Button size="small" type="link" disabled={profileLocked} onClick={() => skillReview.mutate({ skill, reviewStatus: 'REJECTED' })}>排除</Button>
                          )}
                          <Button size="small" type="link" disabled={profileLocked} onClick={() => openSkill(skill)}>编辑</Button>
                          <Popconfirm title="删除这项技能？" onConfirm={() => skillDelete.mutate(skill.id)}>
                            <Button size="small" type="link" danger disabled={profileLocked}>删除</Button>
                          </Popconfirm>
                        </Space>
                      </div>
                      {skill.evidence && (
                        <Typography.Paragraph type="secondary" className="skill-evidence">
                          简历依据：{skill.evidence}
                        </Typography.Paragraph>
                      )}
                    </Card>
                  ))}
                </div>
              )}
            </section>

            <Divider />
            <section aria-labelledby="rules-heading">
              <Typography.Title id="rules-heading" level={4}>4. 全局筛选偏好</Typography.Title>
              <Alert
                className="profile-rule-hint"
                type="info"
                showIcon
                title="这里保存你的长期偏好"
                description="例如排除 MySQL、优先 Python。规则将在 P3-5 的个性化题目筛选中正式生效。"
              />
              <Form<RuleFormValue>
                form={ruleForm}
                layout="inline"
                className="profile-rule-form"
                initialValues={{ ruleType: 'EXCLUDE', scope: 'BOTH' }}
                onFinish={(value) => ruleCreate.mutate(value)}
              >
                <Form.Item name="ruleType" rules={[{ required: true }]}>
                  <Radio.Group optionType="button" buttonStyle="solid">
                    <Radio.Button value="INCLUDE">优先</Radio.Button>
                    <Radio.Button value="EXCLUDE">排除</Radio.Button>
                  </Radio.Group>
                </Form.Item>
                <Form.Item name="technology" rules={[{ required: true, message: '请选择或输入技术' }]}>
                  <AutoComplete
                    allowClear
                    options={technologyOptions}
                    placeholder="选择或输入技术/主题"
                    className="profile-technology-select"
                    filterOption={(input, option) =>
                      String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())
                    }
                  />
                </Form.Item>
                <Form.Item name="scope" rules={[{ required: true }]}>
                  <Select
                    className="profile-scope-select"
                    options={[
                      { value: 'BOTH', label: '题目和答案' },
                      { value: 'QUESTION', label: '仅题目' },
                      { value: 'ANSWER', label: '仅答案' },
                    ]}
                  />
                </Form.Item>
                <Form.Item>
                  <Button type="primary" htmlType="submit" loading={ruleCreate.isPending}>添加</Button>
                </Form.Item>
              </Form>
              {rules.isLoading && <Skeleton active paragraph={{ rows: 3 }} />}
              {rules.isError && <Alert type="error" showIcon title="偏好加载失败" description={rules.error.message} />}
              <div className="profile-rule-list">
                {rules.data?.items.map((rule) => (
                  <div className="profile-rule-row" key={rule.id}>
                    <Space wrap>
                      <Tag color={rule.ruleType === 'INCLUDE' ? 'blue' : 'red'}>
                        {rule.ruleType === 'INCLUDE' ? '优先' : '排除'}
                      </Tag>
                      <Typography.Text strong>{rule.technology}</Typography.Text>
                      <Typography.Text type="secondary">
                        {rule.scope === 'BOTH' ? '题目和答案' : rule.scope === 'QUESTION' ? '仅题目' : '仅答案'}
                      </Typography.Text>
                    </Space>
                    <Space>
                      <Switch
                        size="small"
                        checked={rule.isActive}
                        aria-label={`${rule.technology} 规则是否启用`}
                        onChange={(enabled) => ruleToggle.mutate({ id: rule.id, enabled })}
                      />
                      <Popconfirm title="删除这条偏好？" onConfirm={() => ruleDelete.mutate(rule.id)}>
                        <Button type="link" danger size="small">删除</Button>
                      </Popconfirm>
                    </Space>
                  </div>
                ))}
              </div>
            </section>
          </Space>
        )}
      </Drawer>

      <Modal
        title={editingSkill === 'new' ? '添加技能' : '编辑技能'}
        open={Boolean(editingSkill)}
        okText="保存"
        cancelText="取消"
        confirmLoading={skillSave.isPending}
        onOk={() => skillForm.submit()}
        onCancel={() => setEditingSkill(null)}
        forceRender
      >
        <Form<SkillFormValue> form={skillForm} layout="vertical" onFinish={(value) => skillSave.mutate(value)}>
          <Form.Item label="技能" name="displayName" rules={[{ required: true, whitespace: true }]}>
            <AutoComplete
              options={technologyOptions}
              placeholder="选择或输入技能"
              filterOption={(input, option) =>
                String(option?.label ?? '').toLowerCase().includes(input.toLowerCase())
              }
            />
          </Form.Item>
          <Form.Item label="熟练程度" name="proficiency" rules={[{ required: true }]}>
            <Select options={Object.entries(proficiencyLabels).map(([value, label]) => ({ value, label }))} />
          </Form.Item>
          {editingSkill !== 'new' && (
            <Form.Item label="复核状态" name="reviewStatus" rules={[{ required: true }]}>
              <Select options={Object.entries(reviewLabels).map(([value, label]) => ({ value, label }))} />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </>
  )
}
