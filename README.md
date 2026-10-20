# 面经综合助手

把分散的面经和内部八股知识整理成可检索、可修订的个人问答库。完整架构与后续阶段见 [架构与技术选型设计](./面经综合助手_架构与技术选型设计.md)。

## 当前已支持（P1 + P2）

- 创建面经或知识库资料库。
- 粘贴纯文字/Markdown，或导入 UTF-8 编码的 `.txt`、`.md`、`.markdown` 文件；单份上限 1 MiB。
- 查看原文、修订并保留历史版本和文本块定位。
- 手动建题，或从资料段落建题；答案可以暂时为空。
- 编辑题目和答案，保存或删除私人备注，按题干与答案关键词检索。
- 问题、答案、代码使用不同字体；Markdown 不执行原始 HTML，也不自动加载远程图片。
- 本机固定用户隔离、创建请求幂等、并发编辑版本冲突检查和刷新后持久化。
- 上传 `.png`、`.jpg`、`.jpeg`、`.pdf`、`.docx`、`.doc`；这些二进制文件上限 25 MiB，PDF 最多 100 页。
- 导入无需登录和 JavaScript 的公开静态网页，保存来源 URL、抓取时间与 HTML 快照。
- Celery Worker 后台解析，界面自动刷新等待、处理、部分完成、等待配置和失败状态，并支持最多三次手动重试。
- 图片、扫描 PDF 和 DOCX 内嵌图片通过智谱 `glm-ocr` 识别；未配置 Key 时保留资料并明确提示，不会伪装为成功。
- PDF 文字层、DOCX 段落/标题/表格及旧 `.doc` 转换可在本地完成；旧 `.doc` 由容器内无界面的 LibreOffice 转换。
- 网页导入拒绝本机、私网、保留地址、非标准端口和自动重定向，并限制响应大小与超时。

简历技能提取与个性化筛选属于下一阶段 P3；RAG 和自动生成答案仍属于后续阶段。

## 技术栈

- 前端：React、TypeScript、Vite、Ant Design、TanStack Query
- 后端：FastAPI、SQLAlchemy、Alembic
- 数据：PostgreSQL（pgvector 镜像为后续阶段预留）
- 文档解析：pypdf、python-docx、Trafilatura、LibreOffice
- 图片 OCR：智谱 `glm-ocr`
- 后台任务：Celery、Redis

## 启动

前置要求：Docker 与 Docker Compose。

```bash
cp infra/.env.example infra/.env
docker compose -f infra/docker-compose.yml up -d --build
```

纯文字、文本型 PDF/Word 和网页正文不需要模型 API Key。图片、扫描 PDF 和 Word 内嵌图片 OCR 需要在 `infra/.env` 配置 `GLM_API_KEY`。API 容器启动时会先执行 `alembic upgrade head`，然后启动服务。访问地址：

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
docker compose -f infra/docker-compose.yml exec -T api python scripts/p2_live_smoke.py
```

项目私有原件保存在 `data/`，环境变量保存在 `infra/.env`；两者都被 `.gitignore` 排除。
