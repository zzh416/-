# P2.5 实施计划：统一公开动态网页提取

## 目标

保持单一“网页链接”入口：先使用当前安全静态抓取，正文为空或明显是 JavaScript 外壳时，自动调用隔离的 Playwright 渲染服务，再用现有正文解析器清洗并保存文字与网页快照。

## 边界

- 支持公开、无需登录、一个 URL 能在全新匿名浏览器中自动呈现的单个页面。
- 不继承用户浏览器 Cookie，不处理验证码，不绕过访问限制，不自动爬取整站。
- 暂不为 AI Agent Guide 这类“章节选择不写入 URL”的站点增加专用交互或章节发现。
- 浏览器服务不持有数据库、Redis、文件目录或模型 Key。

## 阶段

### Phase 1：失败测试与接口合约

- **Status:** complete

- 定义渲染结果、稳定错误码和 Worker 自动降级行为。
- 增加静态成功不调用浏览器、静态空壳自动渲染、渲染失败可重试的测试。

### Phase 2：隔离浏览器渲染服务

- **Status:** complete

- 新增独立 Playwright/Chromium 服务和健康检查。
- 对所有导航、脚本、XHR/fetch 和重定向请求做公网地址校验。
- 禁用登录状态、下载、Service Worker、WebSocket、私网访问并限制请求、时间和 DOM 大小。

### Phase 3：Worker 自动降级与界面说明

- **Status:** complete

- 静态提取不足时进入 `RENDERING` 阶段并保存渲染后快照。
- 更新网页导入说明、阶段文案和登录/验证码失败提示。

### Phase 4：容器与端到端验收

- **Status:** complete

- 构建 renderer 镜像，运行单元、后端、前端和 Compose 检查。
- 用公开动态示例验证真实 HTTP→Celery→Playwright→正文链路。
- 更新启动文档、架构状态和 P2.5 验收记录。

## 错误记录

| 错误 | 尝试 | 处理 |
| --- | --- | --- |
| 宿主机 Python 缺少 `pypdf`，无法收集解析测试 | 1 | 改在项目 API 容器中运行，避免污染宿主机环境 |
| Renderer 镜像未复制测试目录，容器中 `npm test` 显示 0 项 | 1 | Dockerfile 复制 `test/`，容器内实际执行 4 项 |
| Docker 默认 seccomp + capability 清空导致 Chromium 沙箱无法启动 | 2 | 使用 Playwright 官方 seccomp profile，并仅补回沙箱所需 `SYS_CHROOT`；保留非 root、只读根目录和 `no-new-privileges` |
| 动态示例页在网络请求完成前被判定稳定，只保存空壳 | 1 | 等待有界 `networkidle`，并提高正文稳定采样次数 |
