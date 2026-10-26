# Infrastructure

本目录集中管理本地运行环境，不存放业务源码或真实用户数据。

- `docker-compose.yml`：稳定模式，包含 Web、API、Worker、Renderer、PostgreSQL 和 Redis。
- `docker-compose.dev.yml`：开发模式源码挂载与热更新覆盖。
- `.env.example`：可提交的配置模板；复制后的 `.env` 含本机 Key，已被 Git 忽略。
- `backup.ps1`：一致性备份 PostgreSQL 与 `data/`。
- `verify-backup.ps1`：在临时数据库和目录中验证备份。
- `restore.ps1`：带恢复前备份和失败回滚的正式恢复。
- `seccomp_profile.json`：Renderer 容器的系统调用限制。

PostgreSQL 与 Redis 的数据分别位于 Docker 命名卷 `pgdata`、`redisdata`。表结构迁移位于 `backend/migrations/`，上传原件位于仓库根目录 `data/`。

从仓库根目录启动：

```powershell
Copy-Item infra\.env.example infra\.env
docker compose -f infra/docker-compose.yml up -d --build
```
