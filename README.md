# 面经综合助手

把分散的面经和内部八股知识整理成可检索、可修订的个人问答库。完整架构与后续阶段见 [架构与技术选型设计](./面经综合助手_架构与技术选型设计.md)。

## P1 已支持

- 创建面经或知识库资料库。
- 粘贴纯文字/Markdown，或导入 UTF-8 编码的 `.txt`、`.md`、`.markdown` 文件；单份上限 1 MiB。
- 查看原文、修订并保留历史版本和文本块定位。
- 手动建题，或从资料段落建题；答案可以暂时为空。
- 编辑题目和答案，保存或删除私人备注，按题干与答案关键词检索。
- 问题、答案、代码使用不同字体；Markdown 不执行原始 HTML，也不自动加载远程图片。
- 本机固定用户隔离、创建请求幂等、并发编辑版本冲突检查和刷新后持久化。

P1 不包含图片 OCR、Word/PDF、网页抓取、简历匹配、RAG 或自动生成答案。这些能力按架构文档中的后续阶段实现。

## 技术栈

- 前端：React、TypeScript、Vite、Ant Design、TanStack Query
- 后端：FastAPI、SQLAlchemy、Alembic
- 数据：PostgreSQL（pgvector 镜像为后续阶段预留）
- 后台任务：Celery、Redis（P1 同步文字导入不依赖队列）

## 启动

前置要求：Docker 与 Docker Compose。

```bash
cp infra/.env.example infra/.env
docker compose -f infra/docker-compose.yml up -d --build
```

P1 不需要模型 API Key。API 容器启动时会先执行 `alembic upgrade head`，然后启动服务。访问地址：

- 前端：http://127.0.0.1:5173
- API 文档：http://127.0.0.1:9000/api/docs
- 存活检查：http://127.0.0.1:9000/api/v1/health/live
- 就绪检查：http://127.0.0.1:9000/api/v1/health/ready

API 和 Web 只监听宿主机的 `127.0.0.1`。停止服务不会删除数据库：

```bash
docker compose -f infra/docker-compose.yml down
```

不要使用 `down -v`，除非确实要永久删除 PostgreSQL 和 Redis 数据卷。

## 更新依赖

前端镜像使用 `package-lock.json` 和 `npm ci`。修改前端依赖后，只刷新 Web 的匿名 `node_modules` 卷，避免删除数据库卷：

```bash
docker compose -f infra/docker-compose.yml build web
docker compose -f infra/docker-compose.yml up -d --no-deps --force-recreate --renew-anon-volumes web
```

后端直接依赖记录在 `apps/api/requirements.txt`，完整解析版本固定在 `apps/api/requirements.lock`，镜像按锁文件安装。升级版本后重建 API 与 Worker，并运行完整测试。

## 验证

```bash
docker compose -f infra/docker-compose.yml exec -T api pytest -q
docker compose -f infra/docker-compose.yml exec -T web npm run lint
docker compose -f infra/docker-compose.yml exec -T web npm run build
docker compose -f infra/docker-compose.yml config --quiet
```

项目私有原件保存在 `data/`，环境变量保存在 `infra/.env`；两者都被 `.gitignore` 排除。
