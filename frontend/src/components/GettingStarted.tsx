import { Button } from 'antd'
import { useState } from 'react'

const preferenceKey = 'bagu.getting-started.collapsed'

interface Props {
  onImport: () => void
  onOrganize: () => void
  onProfile: () => void
  onReview: () => void
  onInterview: () => void
  onGuide: () => void
}

export function GettingStarted({ onImport, onOrganize, onProfile, onReview, onInterview, onGuide }: Props) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const saved = localStorage.getItem(preferenceKey)
      return saved === null ? window.matchMedia('(max-width: 56rem)').matches : saved === 'true'
    } catch { return false }
  })
  const steps = [
    { title: '导入资料', text: '文字、图片、文档或网页链接', action: onImport },
    { title: '整理与复核', text: 'AI提取题答，核对原文与漏题', action: onOrganize },
    { title: '确认画像', text: '确认技能、项目和排除偏好', action: onProfile },
    { title: '开始复习', text: '生成清单，练习与模拟面试', action: onReview },
  ]
  function toggle() {
    const next = !collapsed
    setCollapsed(next)
    try { localStorage.setItem(preferenceKey, String(next)) } catch { /* The guide remains usable without browser storage. */ }
  }
  return <section className="getting-started" aria-labelledby="getting-started-heading">
    <div className="getting-started-heading">
      <div><h2 id="getting-started-heading">上手路线</h2><span>按你的准备情况，选择下一步</span></div>
      <div className="guide-heading-actions">
        <Button type="link" onClick={onGuide}>查看指南</Button>
        <Button type="text" onClick={toggle} aria-expanded={!collapsed} aria-controls="getting-started-steps">{collapsed ? '展开步骤' : '收起步骤'}</Button>
      </div>
    </div>
    {!collapsed && <div id="getting-started-steps">
      <ol className="getting-started-steps">
        {steps.map((step, index) => <li key={step.title}>
          <button type="button" onClick={step.action}>
            <span className="step-number" aria-hidden="true">{index + 1}</span>
            <span><strong>{step.title}</strong><small>{step.text}</small></span>
          </button>
        </li>)}
      </ol>
      <div className="getting-started-shortcut"><span>没有题库也能练习：确认技能或项目后，选择 AI 自主出题。</span><Button type="link" onClick={onInterview}>去模拟面试 →</Button></div>
    </div>}
  </section>
}
