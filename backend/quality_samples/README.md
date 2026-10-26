# 题答抽取金样本

这里的样本用于重复测量题目召回、误抽取、答案状态和答案配对质量，不会写入业务题库。

根目录 JSON 保存已经解析成文字块的样本；`images/` 保存真实图片和对应人工标注。普通样本包含 `name`、`source_format`、`blocks` 和 `expected`，图片标注包含 `name`、`source_format`、`file` 和 `expected`。每个预期题目的 `answer` 为原文答案；资料没有答案时必须写 `null`，不能填占位文本。

运行命令：

```bash
cd backend
python -m tools.evaluate_extraction_quality --samples quality_samples --output quality-report.md
```

该命令会把根目录金样本文字发送到 `TEXT_MODEL_PROVIDER` 指定的 GLM 或 DeepSeek，但默认不会处理 `images/`。增加 `--include-images` 后，图片会发送到 `OCR_PROVIDER` 指定的 GLM 或 DeepSeek，再把 OCR 文字发送到所选文字模型。两个服务商选择相互独立，失败时不自动切换；测试套件不会自动调用外部模型。切换服务商不会改变严格原文引用校验或质量门槛。

使用 DeepSeek 识图时设置 `OCR_PROVIDER=deepseek`、`DEEPSEEK_API_KEY` 和 `DEEPSEEK_OCR_MODEL=deepseek-flash`；转录输出默认上限为 `DEEPSEEK_OCR_MAX_OUTPUT_TOKENS=8192`，输出截断会报错。保留智谱 OCR 则设置 `OCR_PROVIDER=glm`，使用 `GLM_API_KEY` 和 `GLM_OCR_MODEL=glm-ocr`。图片支持 JPG/PNG，10 MiB 上限未扩大。接口依据见 [DeepSeek 官方视觉文档](https://api-docs.deepseek.com/guides/vision/)。

业务解析中的扫描 PDF 和 Word 内嵌图片共用这一 OCR 选择；使用 DeepSeek 时，扫描或含图 PDF 单页先在本地转成 PNG，再提交整页识别；不含图片且文字层可用的 PDF 页面、Word 原生正文仍在本地提取。新版本增加了 Poppler 依赖，升级时须在项目根目录执行 `docker compose -f infra/docker-compose.yml up --build -d api worker`；之后仅修改 `.env` 时执行 `docker compose -f infra/docker-compose.yml up -d --force-recreate api worker`。本目录的 `--include-images` 评测仅读取图片标注，不会自动覆盖扫描 PDF 或 Word 内嵌图的质量验收。

默认门槛为：原始题目召回率 95%、疑似漏题辅助召回率 98%、用户可见题目准确率 98%、答案状态准确率 95%、答案配对准确率 95%。题目采用一对一匹配；只允许长度不少于 8 个字符且字符相似度至少 97% 的 OCR 微小差异，并在报告中单独列出，不能用语义相似度掩盖漏题或错题。
