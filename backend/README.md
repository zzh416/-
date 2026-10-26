# Backend

FastAPI API、Celery Worker、业务逻辑、数据库模型与 Alembic 迁移都位于此目录。

- `app/routes/`：HTTP API
- `app/schemas/`：请求/响应合约
- `app/modules/`：领域逻辑
- `app/adapters/`：模型、OCR 与外部服务适配
- `app/models/`：SQLAlchemy 模型
- `app/workers/`：后台任务
- `migrations/`：数据库迁移
- `tests/`：后端测试

推荐从仓库根目录通过 Docker Compose 启动和测试，避免把测试数据写入正式数据库：

```powershell
docker compose -f infra/docker-compose.yml -f infra/docker-compose.dev.yml run --rm --no-deps api python scripts/run_isolated_tests.py -q
```
