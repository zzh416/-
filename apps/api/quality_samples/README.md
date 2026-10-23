# 题答抽取金样本

这里的样本用于重复测量题目召回、误抽取、答案状态和答案配对质量，不会写入业务题库。

根目录 JSON 保存已经解析成文字块的样本；`images/` 保存真实图片和对应人工标注。普通样本包含 `name`、`source_format`、`blocks` 和 `expected`，图片标注包含 `name`、`source_format`、`file` 和 `expected`。每个预期题目的 `answer` 为原文答案；资料没有答案时必须写 `null`，不能填占位文本。

运行命令：

```bash
cd apps/api
python -m tools.evaluate_extraction_quality --samples quality_samples --output quality-report.md
```

该命令会把根目录金样本文字发送到 `TEXT_MODEL_PROVIDER` 指定的 GLM 或 DeepSeek，但默认不会处理 `images/`。取得资料所有者对本次服务商和处理范围的明确同意后，才可增加 `--include-images`；此参数把图片发送到 GLM OCR，再把 OCR 文字发送到所选文字模型。测试套件不会自动调用外部模型。切换服务商不会改变严格原文引用校验或质量门槛。

默认门槛为：原始题目召回率 95%、疑似漏题辅助召回率 98%、用户可见题目准确率 98%、答案状态准确率 95%、答案配对准确率 95%。题目采用一对一匹配；只允许长度不少于 8 个字符且字符相似度至少 97% 的 OCR 微小差异，并在报告中单独列出，不能用语义相似度掩盖漏题或错题。
