"""Render an already isolated PDF page for image-only OCR providers."""
from __future__ import annotations

import io
import math
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path

from pypdf import PdfReader

from app.adapters.glm_ocr import OCRServiceError


MAX_PDF_BYTES = 50 * 1024 * 1024
MAX_PNG_BYTES = 10 * 1024 * 1024
MAX_IMAGE_SIDE = 2048
RENDER_TIMEOUT_SECONDS = 30
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def render_ocr_pdf_page(content: bytes) -> bytes:
    """Return one bounded PNG; never silently truncate a multi-page input."""
    if not content or len(content) > MAX_PDF_BYTES:
        raise OCRServiceError(
            "OCR_PDF_SIZE_INVALID", "待识别 PDF 页面为空或超过 50 MB 上限", retryable=False
        )
    try:
        reader = PdfReader(io.BytesIO(content), strict=True)
        if reader.is_encrypted:
            raise OCRServiceError(
                "OCR_PDF_ENCRYPTED", "请先解除 PDF 加密后再识别", retryable=False
            )
        if len(reader.pages) != 1:
            raise OCRServiceError(
                "OCR_PDF_PAGE_COUNT_INVALID", "PDF 图片识别每次必须传入恰好一页", retryable=False
            )
        page = reader.pages[0]
        dimensions = (float(page.mediabox.width), float(page.mediabox.height))
        if not all(math.isfinite(value) and value > 0 for value in dimensions):
            raise ValueError("invalid page dimensions")
    except OCRServiceError:
        raise
    except Exception as exc:
        raise OCRServiceError(
            "OCR_PDF_INVALID", "PDF 页面已损坏或无法读取，请重新上传", retryable=False
        ) from exc

    executable = shutil.which("pdftoppm")
    if executable is None:
        raise OCRServiceError(
            "OCR_PDF_RENDER_UNAVAILABLE",
            "缺少 PDF 图片转换组件，请安装 poppler-utils 或重新构建 API 与 worker 镜像",
            retryable=False,
        )

    try:
        with tempfile.TemporaryDirectory(prefix="bagu-ocr-page-") as directory:
            source = Path(directory) / "source.pdf"
            output_prefix = Path(directory) / "page"
            output = output_prefix.with_suffix(".png")
            source.write_bytes(content)
            subprocess.run(
                [
                    executable,
                    "-f", "1", "-l", "1",
                    "-singlefile", "-png",
                    "-scale-to", str(MAX_IMAGE_SIDE),
                    str(source), str(output_prefix),
                ],
                check=True,
                timeout=RENDER_TIMEOUT_SECONDS,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if output.stat().st_size > MAX_PNG_BYTES:
                raise OCRServiceError(
                    "OCR_PDF_RENDER_TOO_LARGE", "PDF 页面转换后的图片超过 10 MB 上限", retryable=False
                )
            with output.open("rb") as rendered:
                png = rendered.read(MAX_PNG_BYTES + 1)
            if len(png) > MAX_PNG_BYTES:
                raise OCRServiceError(
                    "OCR_PDF_RENDER_TOO_LARGE", "PDF 页面转换后的图片超过 10 MB 上限", retryable=False
                )
            if (
                len(png) < 24
                or not png.startswith(PNG_SIGNATURE)
                or png[12:16] != b"IHDR"
                or not all(0 < side <= MAX_IMAGE_SIDE for side in struct.unpack(">II", png[16:24]))
            ):
                raise OCRServiceError(
                    "OCR_PDF_RENDER_INVALID", "PDF 页面转换未生成有效图片，请重新上传", retryable=False
                )
            return png
    except OCRServiceError:
        raise
    except subprocess.TimeoutExpired as exc:
        raise OCRServiceError(
            "OCR_PDF_RENDER_TIMEOUT", "PDF 页面转换超时，请拆分或简化该页面后重试", retryable=False
        ) from exc
    except (OSError, subprocess.SubprocessError) as exc:
        raise OCRServiceError(
            "OCR_PDF_RENDER_FAILED", "PDF 页面转换失败，请检查文件后重新上传", retryable=False
        ) from exc
