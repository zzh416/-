import { Button } from 'antd'
import { useState } from 'react'
import './GettingStarted.css'

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
    { title: '导入资料', text: '文字、图片、文档、网页', action: onImport },
    { title: '整理与复核', text: 'AI提取题答，核对原文与漏题', action: onOrganize },
    { title: '确认画像', text: '确认技能、项目和排除偏好', action: onProfile },
    { title: '开始复习', text: '生成清单，练习与模拟面试', action: onReview },
  ]
  function toggle() {
    const next = !collapsed
    setCollapsed(next)
    try { localStorage.setItem(preferenceKey, String(next)) } catch { /* The guide remains usable without browser storage. */ }
  }
  return <section className={`getting-started${collapsed ? ' is-collapsed' : ''}`} aria-labelledby="getting-started-heading">
    <div className="getting-started-heading">
      <div className="getting-started-title">
        <svg className="route-symbol" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
          <circle cx="5" cy="6" r="2" /><circle cx="19" cy="18" r="2" />
          <path d="M7 6h8a4 4 0 0 1 0 8H9a4 4 0 0 0-4 4h12" />
        </svg>
        <h2 id="getting-started-heading">上手路线</h2>
        <span className="getting-started-description">按你的准备情况，选择下一步</span>
      </div>
      <div className="guide-heading-actions">
        <Button type="link" onClick={onGuide}>查看指南</Button>
        <Button type="text" onClick={toggle} aria-expanded={!collapsed} aria-controls="getting-started-steps">
          {collapsed ? '展开步骤' : '收起步骤'}
          <svg className="route-toggle-icon" width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true" focusable="false"><path d={collapsed ? 'm4 6 4 4 4-4' : 'm4 10 4-4 4 4'} /></svg>
        </Button>
      </div>
    </div>
    <div id="getting-started-steps" hidden={collapsed}>
      <ol className="getting-started-steps">
        {steps.map((step, index) => <li key={step.title}>
          <button type="button" onClick={step.action}>
            <span className="step-number" aria-hidden="true">{String(index + 1).padStart(2, '0')}</span>
            <span><strong>{step.title}</strong><small>{step.text}</small></span>
          </button>
        </li>)}
      </ol>
      <div className="getting-started-shortcut"><span>没有题库也能练习：确认技能或项目后，选择 AI 自主出题。</span><Button type="link" onClick={onInterview}>去模拟面试 →</Button></div>
    </div>
  </section>
}
