"""P2 的确定性文件与网页正文解析器。"""
from __future__ import annotations

import io
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from urllib.parse import urlsplit
from zipfile import ZipFile

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from lxml import html as lxml_html
from pypdf import PdfReader
from pypdf.errors import PdfReadError
from trafilatura import extract


class IngestionParseError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class EmbeddedImage:
    name: str
    content: bytes
    content_type: str


@dataclass(frozen=True)
class ParseResult:
    content: str
    total_units: int = 1
    missing_units: list[int] = field(default_factory=list)
    embedded_images: list[EmbeddedImage] = field(default_factory=list)
    title: str | None = None


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

    sections: list[str] = []
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
        if len(normalized) < 8:
            missing_pages.append(index)
            continue
        sections.append(f"## 第 {index} 页\n\n{normalized}")

    return ParseResult(
        content="\n\n".join(sections),
        total_units=page_count,
        missing_units=missing_pages,
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


def _paragraph_markdown(paragraph: Paragraph) -> str:
    text = paragraph.text.strip()
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


def parse_docx(content: bytes) -> ParseResult:
    try:
        document = Document(io.BytesIO(content))
    except Exception as exc:
        raise IngestionParseError("DOCX_DAMAGED", "DOCX 文件损坏或无法读取") from exc

    blocks: list[str] = []
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            rendered = _paragraph_markdown(block)
        elif isinstance(block, Table):
            rendered = _markdown_table(block)
        else:
            rendered = ""
        if rendered:
            blocks.append(rendered)

    images: list[EmbeddedImage] = []
    try:
        with ZipFile(io.BytesIO(content)) as archive:
            for name in sorted(item for item in archive.namelist() if item.startswith("word/media/")):
                suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                content_type = {
                    "png": "image/png",
                    "jpg": "image/jpeg",
                    "jpeg": "image/jpeg",
                }.get(suffix)
                if content_type:
                    images.append(
                        EmbeddedImage(
                            name=name.rsplit("/", 1)[-1],
                            content=archive.read(name),
                            content_type=content_type,
                        )
                    )
    except Exception as exc:
        raise IngestionParseError("DOCX_DAMAGED", "DOCX 内部资源无法读取") from exc

    result = "\n\n".join(blocks).strip()
    if not result and not images:
        raise IngestionParseError("DOCX_EMPTY", "DOCX 中没有可提取的文字或图片")
    return ParseResult(content=result, embedded_images=images)


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
    if not markdown or len(markdown.strip()) < 20:
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
    return ParseResult(content=markdown.strip(), title=title)
