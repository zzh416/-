export type WorkspaceSection = 'workspace' | 'profile' | 'review' | 'learning' | 'interview'

const entries: { key: WorkspaceSection; title: string; detail: string; icon: string }[] = [
  { key: 'workspace', title: '资料工作台', detail: '导入资料 · 整理题库', icon: 'M4 5h6l2 2h8v13H4z M8 11h8 M8 15h6' },
  { key: 'profile', title: '我的画像', detail: '简历 · 技能 · 项目 · 偏好', icon: 'M16 7a4 4 0 1 1-8 0a4 4 0 0 1 8 0 M5 21v-2a7 7 0 0 1 14 0v2' },
  { key: 'review', title: '复习清单', detail: '按技能和偏好筛选', icon: 'M9 5h11 M9 12h11 M9 19h11 M3 5l1 1 2-2 M3 12l1 1 2-2 M3 19l1 1 2-2' },
  { key: 'learning', title: '薄弱题复习', detail: '巩固不会与不熟悉的题', icon: 'M20 12a8 8 0 1 1-4-7 M16 12a4 4 0 1 1-4-4 M12 12l8-8 M17 4h3v3' },
  { key: 'interview', title: '模拟面试', detail: '限时作答 · 反馈 · 追问', icon: 'M4 4h16v12H9l-5 4z M8 8h8 M8 12h5' },
]

interface Props {
  active: WorkspaceSection
  onNavigate: (section: WorkspaceSection) => void
}

export function WorkspaceNav({ active, onNavigate }: Props) {
  return <nav className="workspace-nav" aria-label="主要功能">
    {entries.map((entry) => <button key={entry.key} type="button"
      className={`workspace-nav-item nav-${entry.key}${active === entry.key ? ' is-active' : ''}`}
      aria-current={active === entry.key ? 'page' : undefined}
      onClick={() => onNavigate(entry.key)}>
      <span className="nav-icon"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={entry.icon} /></svg></span>
      <span className="nav-copy"><strong>{entry.title}</strong><small>{entry.detail}</small></span>
    </button>)}
  </nav>
}
