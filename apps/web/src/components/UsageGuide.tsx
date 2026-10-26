import { Button, Drawer, Space, Tabs, Tag } from 'antd'
import type { WorkspaceSection } from './WorkspaceNav'

interface Props {
  open: boolean
  onClose: () => void
  onNavigate: (section: WorkspaceSection) => void
}

export function UsageGuide({ open, onClose, onNavigate }: Props) {
  function go(section: WorkspaceSection) { onClose(); onNavigate(section) }
  return <Drawer title="使用指南" open={open} onClose={onClose} size={760} className="usage-guide-drawer" styles={{ wrapper: { maxWidth: '100vw' } }}>
    <p className="guide-intro">把资料变成适合自己的复习题，也可以直接从简历开始模拟面试。</p>
    <Tabs items={[
      { key: 'start', label: '从这里开始', children: <div className="usage-guide-content">
        <section className="guide-path"><h3>有面经或八股资料</h3><p>导入资料 → 整理并复核题答 → 设置画像 → 生成复习清单 → 复习或模拟面试。</p><Button onClick={() => go('workspace')}>打开资料工作台</Button></section>
        <section className="guide-path"><h3>暂时没有题库</h3><p>上传简历或手动填写技能、项目，确认后打开模拟面试，选择“AI 自主出题”。后续随时可以补充资料。</p><Space wrap><Button type="primary" onClick={() => go('profile')}>完善我的画像</Button><Button onClick={() => go('interview')}>打开模拟面试</Button></Space></section>
        <ol className="guide-detailed-steps">
          <li><h3>创建资料库，导入材料</h3><p>按“面经”或“知识库”分开管理。支持粘贴文字、图片、PDF、Word、单个网页和批量链接。资料卡片显示解析状态，完成后打开查看正文。</p></li>
          <li><h3>整理题目，核对结果</h3><p>在资料详情中点“AI 自动整理”，再进入复核。对照原文检查题答配对，留意“疑似漏题”；没有原文答案的题会保留为无答案题。</p></li>
          <li><h3>确认画像，生成清单</h3><p>在“我的画像”确认技能与项目，并添加排除或优先偏好。打开“复习清单”生成或更新结果，可查看每道题的筛选原因。修改画像、偏好或题库后，按页面提示重新生成清单。</p></li>
          <li><h3>按自己的节奏练习</h3><p>手动标记“不会 / 不熟悉 / 已掌握”，在薄弱题中集中练习。模拟面试可选题库、AI自主或混合出题，调整每题时长；是否追问由你决定，不会时可切换方向。</p></li>
        </ol>
      </div> },
      { key: 'answers', label: '答案与AI的区别', children: <div className="usage-guide-content">
        <h3>每份答案都有明确来源</h3>
        <dl className="guide-answer-types">
          <div><dt><Tag>资料答案</Tag></dt><dd>从上传资料中提取，阅读时可以查看关联原文。自动整理不会把模型知识补成原文答案。</dd></div>
          <div><dt><Tag>手动答案</Tag></dt><dd>由你自行填写或修改，适合保存自己的理解和表达方式。</dd></div>
          <div><dt><Tag color="blue">AI 补充答案</Tag></dt><dd>由你主动触发生成，先作为草稿供检查，可编辑后确认或丢弃。确认后仍保留 AI 来源标识，并与资料答案区分。</dd></div>
        </dl>
        <h3>AI 补答案怎么用</h3><p>在题目处点击“AI 补充答案”，阅读草稿并检查关键结论，再确认保存。草稿不会自动作为正式复习答案；已有资料或手写答案会保留。</p>
        <h3>与模拟面试反馈有什么不同</h3><p>补充答案提供这道题的参考解答；面试反馈针对你这次的作答，指出覆盖点与可改进之处。参考答案不代表你的实际项目经历，涉及个人经历的内容需要自行核实和补充。</p>
        <p className="guide-note">资料抽取、AI出题、补答案和面试反馈使用已配置的模型服务，可能产生API费用。生成时查看页面中的提示，遇到配置或余额问题可按错误提示处理后重试。</p>
      </div> },
      { key: 'help', label: '常见问题', children: <div className="usage-guide-content guide-faq">
        <details open><summary>排除技能后，题库里的题为什么没有减少？</summary><p>偏好作用于个性化复习清单，不会删除原始题库。修改偏好后重新生成清单，并查看“已排除”和筛选依据。</p></details>
        <details><summary>网页打不开、需要登录，或者没有提取到正文？</summary><p>优先使用公开文章的正文链接。页面依赖登录、验证码或无法正常抓取时，可以复制正文粘贴导入，也可以上传截图或文档。</p></details>
        <details><summary>自动整理漏题，或把作者说明当成题目怎么办？</summary><p>进入资料的复核界面，对照原文检查“疑似漏题”，补入有效题并拒绝说明文字。复核后再用于复习，手动修订会保留。</p></details>
        <details><summary>AI出题没有达到设置数量怎么办？</summary><p>打开“出题检查详情”查看原因。未开始作答且仍有缺额时，可点击“补足目标题数”；确认项目与技能描述足够具体，并检查排除偏好。</p></details>
        <details><summary>项目筛选和技能排除是什么关系？</summary><p>先按技能和排除偏好确定适合你的题，再给出项目关联建议。未匹配项目的题仍可作为通用基础题复习。</p></details>
        <details><summary>“不会”“不熟悉”“已掌握”是谁判断的？</summary><p>这是你的学习标记，可随时调整。面试中选择不会后会给出加入薄弱复习的建议，需要你确认。</p></details>
      </div> },
    ]} />
  </Drawer>
}
