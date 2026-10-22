# 面经综合助手

把分散的面经和内部八股知识整理成可检索、可修订的个人问答库。完整架构与后续阶段见 [架构与技术选型设计](./面经综合助手_架构与技术选型设计.md)，下一步的详细拆分见 [P3 计划与下一版本路线](./docs/P3计划与下一版本路线.md)。

## 当前已支持（P1、P2 与 P3 已完成部分）

- 创建面经或知识库资料库。
- 粘贴纯文字/Markdown，或导入 UTF-8 编码的 `.txt`、`.md`、`.markdown` 文件；单份上限 1 MiB。
- 查看原文、修订并保留历史版本和文本块定位。
- 手动建题，或从资料段落建题；答案可以暂时为空。
- 编辑题目和答案，保存或删除私人备注，按题干与答案关键词检索。
- 问题、答案、代码使用不同字体；Markdown 不执行原始 HTML，也不自动加载远程图片。
- 本机固定用户隔离、创建请求幂等、并发编辑版本冲突检查和刷新后持久化。
- 上传 `.png`、`.jpg`、`.jpeg`、`.pdf`、`.docx`、`.doc`；这些二进制文件上限 25 MiB，PDF 最多 100 页。
- 通过同一个链接入口导入公开网页：先提取静态正文，遇到 JavaScript 空壳时自动使用隔离的 Chromium 渲染，再保存来源 URL、抓取时间与 HTML 快照。
- Celery Worker 后台解析，界面自动刷新等待、处理、部分完成、等待配置和失败状态，并支持最多三次手动重试。
- 图片、扫描 PDF 和 DOCX 内嵌图片通过智谱 `glm-ocr` 识别；未配置 Key 时保留资料并明确提示，不会伪装为成功。
- PDF 文字层、DOCX 段落/标题/表格及旧 `.doc` 转换可在本地完成；旧 `.doc` 由容器内无界面的 LibreOffice 转换。
- 网页导入拒绝本机、私网、保留地址和非标准端口；静态请求与浏览器请求都会检查目标地址，并限制请求数、响应大小和超时。
- 链接导入只处理匿名可访问的单个页面；需要登录、验证码或当前章节没有体现在 URL 中时，界面会提示改用粘贴文字或上传文件。
- 使用 GLM 从当前资料版本自动整理题目；模型结果必须逐字回指原文，单批失败时会保留其他批次的有效结果。
- 自动区分有答案和无答案问题，不为缺失答案生成占位内容；支持按答案状态、复核状态和关键词筛选。
- 对照原文逐题确认、修改、拒绝或重置，并通过确定性检查列出疑似漏题，允许从准确原文范围补建。
- 在独立的“我的画像”中上传和版本化管理简历，提取带原文证据的目标岗位和技能；支持确认、排除、编辑和补充技能。
- 管理全局优先/排除偏好及其题目/答案作用范围，并支持技术目录和自定义技术项。
- 生成版本化的“我的复习清单”：题干和答案分别识别技术依赖，显示进入、排除、排序及答案不适用原因；画像、规则或题库内容变化后旧结果会提示重新生成。
- 自动整理失败或部分成功时可安全重试，最多三次；重复请求不会重复建题，也不会覆盖已复核或人工修改内容。

P3 尚未全部完成。个性化清单不会改写基础题库，用户可以随时回看被排除的题目及原因。下一步补齐抽取复核增强项和 P3 总验收。当前版本不提供 AI 答案生成，按需生成缺失答案留到下一版本。

## 技术栈

- 前端：React、TypeScript、Vite、Ant Design、TanStack Query
- 后端：FastAPI、SQLAlchemy、Alembic
- 数据：PostgreSQL（pgvector 镜像为后续阶段预留）
- 文档解析：pypdf、python-docx、Trafilatura、LibreOffice
- 动态网页：Playwright、Chromium（独立无密钥容器）
- 图片 OCR：智谱 `glm-ocr`
- 后台任务：Celery、Redis

## 启动

前置要求：Docker 与 Docker Compose。

```bash
cp infra/.env.example infra/.env
docker compose -f infra/docker-compose.yml up -d --build
```

纯文字、文本型 PDF/Word 和网页正文的基础解析不需要模型 API Key。图片、扫描 PDF、Word 内嵌图片 OCR，以及 AI 自动整理题目和简历画像提取，需要在 `infra/.env` 配置 `GLM_API_KEY`。API 容器启动时会先执行 `alembic upgrade head`，然后启动服务。访问地址：

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
docker compose -f infra/docker-compose.yml exec -T renderer npm test
docker compose -f infra/docker-compose.yml exec -T web npm run lint
docker compose -f infra/docker-compose.yml exec -T web npm run build
docker compose -f infra/docker-compose.yml config --quiet
docker compose -f infra/docker-compose.yml exec -T api python scripts/p2_live_smoke.py
```

项目私有原件保存在 `data/`，环境变量保存在 `infra/.env`；两者都被 `.gitignore` 排除。
