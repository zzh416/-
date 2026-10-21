"""P2 的确定性文件与网页正文解析器。"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from docx import Document
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import html as lxml_html
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from trafilatura import extract


class IngestionParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class SourceBlock:
    block_type: str
    content: str
    locator: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OCRUnit:
    block_index: int
    label: str
    content: bytes
    content_type: str
    locator: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ParseResult:
    content: str
    total_units: int = 1
    missing_units: list[int] = field(default_factory=list)
    blocks: list[SourceBlock] = field(default_factory=list)
    ocr_units: list[OCRUnit] = field(default_factory=list)
    title: str | None = None


def _single_page_pdf(page: Any) -> bytes:
    output = io.BytesIO()
    writer = PdfWriter()
    writer.add_page(page)
    writer.write(output)
    return output.getvalue()


def _resources_have_images(resources: Any) -> bool:
    if resources is None:
        return False
    try:
        resolved_resources = resources.get_object()
        xobjects = resolved_resources.get("/XObject")
        if xobjects is None:
            return False
        for item in xobjects.get_object().values():
            resolved = item.get_object()
            subtype = resolved.get("/Subtype")
            if subtype == "/Image":
                return True
            if subtype == "/Form" and _resources_have_images(resolved.get("/Resources")):
                return True
        return False
    except Exception:
        # 无法安全判断时宁可让这一页进入 OCR，也不要静默漏掉截图。
        return True


def render_source_blocks(blocks: list[SourceBlock]) -> str:
    sections = []
    for block in blocks:
        normalized = block.content.strip()
        if not normalized:
            continue
        if block.block_type == "PDF_PAGE":
            sections.append(f"## 第 {block.locator.get('page')} 页\n\n{normalized}")
        elif block.block_type == "DOCX_IMAGE":
            sections.append(f"## 内嵌图片：{block.locator.get('image', '图片')}\n\n{normalized}")
        else:
            sections.append(normalized)
    return "\n\n".join(sections)


def parse_pdf(
    content: bytes,
    *,
    max_pages: int,
    max_content_stream_bytes: int,
) -> ParseResult:
    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted:
            raise IngestionParseError("PDF_ENCRYPTED", "PDF 已加密，请先解除密码后重新上传")
        page_count = len(reader.pages)
    except IngestionParseError:
        raise
    except (PdfReadError, ValueError, OSError) as exc:
        raise IngestionParseError("PDF_DAMAGED", "PDF 文件损坏或无法读取") from exc

    if page_count == 0:
        raise IngestionParseError("PDF_EMPTY", "PDF 没有可处理的页面")
    if page_count > max_pages:
        raise IngestionParseError(
            "PDF_TOO_MANY_PAGES",
            f"PDF 共 {page_count} 页，超过 {max_pages} 页上限",
        )

    blocks: list[SourceBlock] = []
    ocr_units: list[OCRUnit] = []
    missing_pages: list[int] = []
    for index, page in enumerate(reader.pages, start=1):
        try:
            page_contents = page.get_contents()
            if page_contents is not None:
                stream_size = len(page_contents.get_data())
                if stream_size > max_content_stream_bytes:
                    raise IngestionParseError(
                        "PDF_CONTENT_STREAM_TOO_LARGE",
                        f"PDF 第 {index} 页的内容流过大，已停止解析",
                    )
            text = page.extract_text(extraction_mode="layout") or ""
        except IngestionParseError:
            raise
        except Exception as exc:
            raise IngestionParseError(
                "PDF_PAGE_PARSE_FAILED",
                f"PDF 第 {index} 页无法解析",
            ) from exc
        normalized = text.strip()
        block_index = len(blocks)
        blocks.append(
            SourceBlock(
                block_type="PDF_PAGE",
                content=normalized,
                locator={"page": index},
            )
        )
        is_missing = len(normalized) < 8
        has_images = _resources_have_images(page.get("/Resources"))
        if is_missing:
            missing_pages.append(index)
        if is_missing or has_images:
            ocr_units.append(
                OCRUnit(
                    block_index=block_index,
                    label=f"第 {index} 页",
                    content=_single_page_pdf(page),
                    content_type="application/pdf",
                    locator={
                        "page": index,
                        "reason": "MISSING_TEXT" if is_missing else "EMBEDDED_IMAGE",
                    },
                )
            )

    return ParseResult(
        content=render_source_blocks(blocks),
        total_units=page_count,
        missing_units=missing_pages,
        blocks=blocks,
        ocr_units=ocr_units,
    )


def _markdown_table(table: Table) -> str:
    rows: list[list[str]] = []
    for row in table.rows:
        values = [cell.text.strip().replace("|", "\\|").replace("\n", "<br>") for cell in row.cells]
        if any(values):
            rows.append(values)
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [row + [""] * (width - len(row)) for row in rows]
    lines = [
        "| " + " | ".join(normalized[0]) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in normalized[1:])
    return "\n".join(lines)


def _paragraph_text_markdown(paragraph: Paragraph, text: str) -> str:
    text = text.strip()
    if not text:
        return ""
    style_name = paragraph.style.name if paragraph.style is not None else ""
    if style_name.lower().startswith("heading"):
        suffix = style_name.removeprefix("Heading").strip()
        level = int(suffix) if suffix.isdigit() else 2
        return f"{'#' * max(1, min(level, 6))} {text}"
    if style_name.lower().startswith("title"):
        return f"# {text}"
    return text


def _paragraph_markdown(paragraph: Paragraph) -> str:
    return _paragraph_text_markdown(paragraph, paragraph.text)


def _image_relationship_ids(element: Any) -> list[str]:
    relationship_ids: list[str] = []
    for blip in element.xpath(".//a:blip"):
        relationship_id = blip.get(qn("r:embed"))
        if relationship_id:
            relationship_ids.append(relationship_id)
    return relationship_ids


def _append_docx_image(
    document: Any,
    relationship_id: str,
    blocks: list[SourceBlock],
    ocr_units: list[OCRUnit],
    locator: dict[str, Any],
) -> None:
    part = document.part.related_parts.get(relationship_id)
    if part is None or part.content_type not in {"image/png", "image/jpeg"}:
        return
    name = str(part.partname).rsplit("/", 1)[-1]
    image_locator = {**locator, "relationshipId": relationship_id, "image": name}
    block_index = len(blocks)
    blocks.append(SourceBlock("DOCX_IMAGE", "", image_locator))
    ocr_units.append(
        OCRUnit(
            block_index=block_index,
            label=f"内嵌图片：{name}",
            content=part.blob,
            content_type=part.content_type,
            locator=image_locator,
        )
    )


def _append_docx_paragraph_text(
    paragraph: Paragraph,
    text: str,
    blocks: list[SourceBlock],
    *,
    paragraph_index: int,
    segment: int | None = None,
) -> None:
    rendered = _paragraph_text_markdown(paragraph, text)
    if not rendered:
        return
    locator: dict[str, Any] = {"paragraph": paragraph_index}
    if segment is not None:
        locator["segment"] = segment
    blocks.append(SourceBlock("DOCX_PARAGRAPH", rendered, locator))


def parse_docx(content: bytes) -> ParseResult:
    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:
        raise IngestionParseError("DOCX_DAMAGED", "DOCX 文件损坏或无法读取") from exc

    blocks: list[SourceBlock] = []
    ocr_units: list[OCRUnit] = []
    paragraph_index = 0
    table_index = 0
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            paragraph_index += 1
            relationship_ids = _image_relationship_ids(block._element)
            if not relationship_ids:
                _append_docx_paragraph_text(
                    block,
                    block.text,
                    blocks,
                    paragraph_index=paragraph_index,
                )
                continue

            pending_text: list[str] = []
            segment = 0
            for run in block.runs:
                if run.text:
                    pending_text.append(run.text)
                run_images = _image_relationship_ids(run._element)
                if not run_images:
                    continue
                if pending_text:
                    segment += 1
                    _append_docx_paragraph_text(
                        block,
                        "".join(pending_text),
                        blocks,
                        paragraph_index=paragraph_index,
                        segment=segment,
                    )
                    pending_text = []
                for relationship_id in run_images:
                    _append_docx_image(
                        document,
                        relationship_id,
                        blocks,
                        ocr_units,
                        {"paragraph": paragraph_index},
                    )
            if pending_text:
                segment += 1
                _append_docx_paragraph_text(
                    block,
                    "".join(pending_text),
                    blocks,
                    paragraph_index=paragraph_index,
                    segment=segment,
                )
        elif isinstance(block, Table):
            table_index += 1
            rendered = _markdown_table(block)
            if rendered:
                blocks.append(SourceBlock("DOCX_TABLE", rendered, {"table": table_index}))
            for relationship_id in _image_relationship_ids(block._element):
                _append_docx_image(
                    document,
                    relationship_id,
                    blocks,
                    ocr_units,
                    {"table": table_index},
                )

    result = "\n\n".join(block.content for block in blocks if block.content).strip()
    if not result and not ocr_units:
        raise IngestionParseError("DOCX_EMPTY", "DOCX 中没有可提取的文字或图片")
    return ParseResult(content=result, blocks=blocks, ocr_units=ocr_units)


def convert_legacy_doc(content: bytes, *, timeout_seconds: float = 60.0) -> bytes:
    executable = shutil.which("libreoffice") or shutil.which("soffice")
    if executable is None:
        raise IngestionParseError(
            "DOC_CONVERTER_UNAVAILABLE",
            "当前环境没有旧 Word 转换器，请将 .doc 另存为 .docx 后重新上传",
        )
    with tempfile.TemporaryDirectory(prefix="bagu-doc-") as temp_directory:
        source = os.path.join(temp_directory, "source.doc")
        destination = os.path.join(temp_directory, "source.docx")
        with open(source, "wb") as output:
            output.write(content)
        environment = {
            "HOME": temp_directory,
            "PATH": os.environ.get("PATH", ""),
            "LANG": "C.UTF-8",
        }
        try:
            completed = subprocess.run(
                [
                    executable,
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    temp_directory,
                    source,
                ],
                cwd=temp_directory,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise IngestionParseError("DOC_CONVERSION_TIMEOUT", "旧 Word 转换超时") from exc
        if completed.returncode != 0 or not os.path.isfile(destination):
            raise IngestionParseError(
                "DOC_CONVERSION_FAILED",
                "旧 Word 文件转换失败，请另存为 DOCX 或 PDF 后重新上传",
            )
        if os.path.getsize(destination) > 100 * 1024 * 1024:
            raise IngestionParseError("DOC_CONVERSION_TOO_LARGE", "旧 Word 转换结果超过大小上限")
        with open(destination, "rb") as converted:
            return converted.read()


def extract_webpage(content: bytes, source_url: str) -> ParseResult:
    try:
        markdown = extract(
            content,
            url=source_url,
            output_format="markdown",
            include_comments=False,
            include_tables=True,
            include_links=True,
            favor_precision=True,
        )
    except Exception as exc:
        raise IngestionParseError("WEB_EXTRACTION_FAILED", "网页正文提取失败") from exc
    normalized_markdown = " ".join(markdown.split()).lower() if markdown else ""
    javascript_shell_phrases = (
        "enable javascript to run this app",
        "enable javascript to continue",
        "javascript is required",
        "请启用 javascript",
        "请开启 javascript",
        "需要启用 javascript",
    )
    is_javascript_shell = (
        len(normalized_markdown) < 500
        and any(phrase in normalized_markdown for phrase in javascript_shell_phrases)
    )
    if not markdown or len(markdown.strip()) < 20 or is_javascript_shell:
        raise IngestionParseError(
            "WEB_CONTENT_EMPTY",
            "没有提取到可用正文；该网页可能需要登录或 JavaScript，可改用粘贴文字",
        )

    title: str | None = None
    try:
        tree = lxml_html.fromstring(content)
        title_values = tree.xpath("//title/text()")
        if title_values:
            title = " ".join(title_values[0].split())[:200]
    except Exception:
        title = None
    if not title:
        title = urlsplit(source_url).hostname
    normalized = markdown.strip()
    return ParseResult(
        content=normalized,
        blocks=[SourceBlock("WEB_DOCUMENT", normalized, {"url": source_url})],
        title=title,
    )
