# P2 发现与决策

## 已确认现状

- P1 的 `Asset`、`AssetVersion`、`TextChunk` 已提供资料版本和文字纠错基础。
- P1 上传只接受 UTF-8 TXT/Markdown，最大 1 MiB，并同步创建文字版本。
- Celery/Redis 已运行，但当前 Worker 只有基础占位任务。
- P2 设计明确包含图片、PDF、DOCX、旧 DOC、静态网页正文及图片、任务进度、来源与纠错。
- P3 才负责简历技能和全局偏好，不混入 P2 数据模型。

## 威胁模型

- 文件边界：伪造扩展名/MIME、压缩炸弹、超大文件、恶意文件名、路径穿越、解析器资源耗尽。
- URL 边界：SSRF、DNS 重绑定、重定向到私网、超大响应、非 HTML 内容、慢速响应。
- Worker 边界：重复任务、任务中止后回写已删除资料、异常泄露内部路径或密钥。
- 模型边界：网页/文档提示注入、畸形 JSON、超长输出、供应商错误泄露。
- 展示边界：解析内容继续通过安全 Markdown 展示，不执行 HTML、脚本或远程图片。

## 初始技术决策

- 原件写入 `DATA_DIR` 下按 owner/asset 生成的服务端路径，数据库不接受客户端路径。
- 后端对扩展名、声明 MIME 和魔数交叉校验；所有格式统一使用配置化 25 MiB 上限。
- 解析任务用数据库状态作为事实来源，Celery 只承载执行，不把完整文件内容放入消息。
- URL 首版禁用自动重定向；如需跟随，必须逐跳重新做 DNS/IP 检查。
- P2 测试不调用真实外部网站或 OCR 服务，使用受控响应；最终仅在已有 Key 时进行可选联调。

## 官方文档核对

- pypdf 可提取数字原生 PDF 的文本，但官方明确说明它不是 OCR；扫描 PDF 必须走图像/OCR 路径。
- pypdf 官方警告单页未压缩内容流可能导致很高内存消耗，因此解析前必须检查页数和内容流大小，不能只限制上传文件大小。
- python-docx 1.2 提供 `iter_inner_content()`，可按文档顺序遍历段落和表格，适合保留正文顺序。
- Trafilatura 2.3 的 `extract()`/`bare_extraction()` 可直接处理已获取的 HTML 并输出 Markdown；网络获取由本项目基于 Python 标准库实现的固定 IP 客户端完成，不能直接使用其不带本项目 SSRF 策略的便捷下载函数。
- 智谱官方文档索引确认 `GLM-OCR`：`POST /api/paas/v4/layout_parsing`，请求包含 `model=glm-ocr` 和 URL/base64 `file`，响应核心字段为 `md_results`、`layout_details`、`data_info`；图片 ≤10 MiB，PDF ≤50 MiB、≤100 页。
- 本机当前未配置 `GLM_API_KEY`。实现和受控测试可继续，真实图片/扫描 PDF 联调需用户稍后在本机 `.env` 配置，密钥不得进入聊天、日志或 Git。

## 最终实现决策

- API、Celery Worker 和 LibreOffice 子进程在容器中以 UID 10001 的 `appuser` 运行；入口进程只在启动时修正绑定数据目录权限，然后立即降权。
- Worker 并发固定为 1，避免个人电脑同时运行多个 OCR 或 LibreOffice 转换任务。
- 网页连接不会自动跟随重定向；DNS 全量校验后固定连接到已验证公网 IP，同时保留 HTTPS SNI 与主机名证书校验。
- 任务终态区分 `READY`、`PARTIAL`、`WAITING_FOR_CONFIGURATION` 和 `FAILED`，让缺少 OCR Key 与解析失败具有不同的恢复路径。

