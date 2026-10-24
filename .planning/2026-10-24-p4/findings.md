# P4 调研记录

- P3 主链路和当前样本质量验收已经完成，但 `tasks/todo.md`、README 与架构文档仍保留过期状态。
- Compose 当前使用 Uvicorn/Vite 热更新并挂载源码，适合开发，不适合作为默认稳定运行形态。
- PostgreSQL 位于 Docker 命名卷，上传原件位于项目 `data/`；可靠备份必须同时覆盖两者。
- API 已限制 Origin、Host 和本机端口，但尚缺请求关联 ID、安全响应头和统一访问日志。
- 已有测试覆盖 owner 隔离、幂等、模型输出污染、SSRF 和资源上限；P4 应补充交付层回归而不是重写这些能力。
- 首次 Python 漏洞审计发现 `pypdf 6.21.1` 的 3 个 CVE，升级到 6.21.2 后复审为 0。
- 默认稳定模式直接以 `appuser` 运行 API/Worker；这样无需为入口切换用户保留 SETUID/SETGID capability，宿主机 `data/` 在 Docker Desktop 绑定挂载下仍可正常写入。
- 前端构建包约 1.17 MB（gzip 368 KB），当前本机验收不阻塞，页面继续增长时再做按模块拆包。
