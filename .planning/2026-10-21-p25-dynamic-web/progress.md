# P2.5 进度日志

## 2026-10-21

- 用户确认优先解决知乎、CSDN、公开语雀、GitHub 等主流公开页面的统一提取，私人章节站点另行讨论。
- 范围确定为静态优先、Playwright 自动降级、匿名单页、不登录、不处理验证码、不整站爬取。
- 已检查现有静态抓取、解析、Worker、Compose 与前端阶段文案，开始 Phase 1。
- 已新增受限 renderer 服务、Python 客户端和 Worker 自动降级，并增加 JavaScript 空壳识别。
- 合并前审查补充了重定向、匿名 403 和压缩响应到浏览器降级；私网、限流、超限仍直接拒绝。
- 后端完整基准 47 项通过；增加重定向回归后，受影响的 14 项测试通过。前端 lint 无错误，生产构建成功。
- Playwright 基础镜像正在首次下载；完成后进行浏览器沙箱、真实动态页面与内网阻断验收。
- Playwright 镜像构建完成，依赖审计 0 个漏洞；renderer 容器内 4 项安全测试通过。
- 使用 Playwright 官方 seccomp 配置和最小 `SYS_CHROOT` capability 后，Chromium 沙箱成功运行；`127.0.0.1` 返回 `WEB_PRIVATE_ADDRESS`。
- 公开动态示例渲染出约 28 KB DOM 并包含脚本加载正文；真实队列冒烟的 DOCX、旧 DOC、静态网页和动态网页全部 READY，临时数据已清理。
- 最终后端 48 项通过，renderer 4 项通过，前端 lint 无错误且生产构建成功，Compose 配置通过；浏览器确认入口文案和限制提示正确。
