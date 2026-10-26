# Services

此目录存放与主业务后端分离、可独立构建和限制权限的辅助服务。

- `renderer/`：使用 Playwright/Chromium 渲染公开 JavaScript 网页。它没有业务数据库访问权限，只通过内部网络向 Worker 提供受限的页面提取能力。

PostgreSQL 和 Redis 属于基础设施，由 `infra/docker-compose.yml` 管理，因此不放在此目录。
