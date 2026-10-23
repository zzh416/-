import base64
import io
import shutil
import struct
import subprocess
from pathlib import Path

import pytest
from pypdf import PdfWriter

from app.adapters.glm_ocr import OCRServiceError
from app.modules import ocr_pdf_render


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a0Y4AAAAASUVORK5CYII="
)


def pdf_bytes(pages: int = 1, *, encrypted: bool = False) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    if encrypted:
        writer.encrypt("private-password")
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


def test_pdf_render_uses_bounded_single_page_command_and_cleans_up(monkeypatch) -> None:
    captured = {}

    def run(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        assert Path(args[-2]).read_bytes() == content
        Path(args[-1]).with_suffix(".png").write_bytes(PNG)

    content = pdf_bytes()
    monkeypatch.setattr(ocr_pdf_render.shutil, "which", lambda _: "/usr/bin/pdftoppm")
    monkeypatch.setattr(ocr_pdf_render.subprocess, "run", run)
    assert ocr_pdf_render.render_ocr_pdf_page(content) == PNG
    assert captured["args"][1:-2] == [
        "-f", "1", "-l", "1", "-singlefile", "-png", "-scale-to", "2048"
    ]
    assert captured["kwargs"]["timeout"] == 30
    assert captured["kwargs"]["check"] is True
    assert not captured["kwargs"].get("shell", False)
    assert not Path(captured["args"][-2]).parent.exists()


@pytest.mark.parametrize(
    ("content", "code"),
    [
        (pdf_bytes(0), "OCR_PDF_PAGE_COUNT_INVALID"),
        (pdf_bytes(2), "OCR_PDF_PAGE_COUNT_INVALID"),
        (pdf_bytes(encrypted=True), "OCR_PDF_ENCRYPTED"),
        (b"%PDF-1.7\nprivate-document-body", "OCR_PDF_INVALID"),
        (b"", "OCR_PDF_SIZE_INVALID"),
    ],
)
def test_pdf_render_rejects_invalid_input_before_launch(monkeypatch, content, code) -> None:
    monkeypatch.setattr(
        ocr_pdf_render.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("must not render")
    )
    with pytest.raises(OCRServiceError) as error:
        ocr_pdf_render.render_ocr_pdf_page(content)
    assert error.value.code == code
    assert error.value.retryable is False
    assert "private-document-body" not in str(error.value)


def test_pdf_render_input_limit(monkeypatch) -> None:
    monkeypatch.setattr(ocr_pdf_render, "MAX_PDF_BYTES", 10)
    with pytest.raises(OCRServiceError) as error:
        ocr_pdf_render.render_ocr_pdf_page(pdf_bytes())
    assert error.value.code == "OCR_PDF_SIZE_INVALID"


def test_pdf_render_missing_binary_has_actionable_message(monkeypatch) -> None:
    monkeypatch.setattr(ocr_pdf_render.shutil, "which", lambda _: None)
    with pytest.raises(OCRServiceError) as error:
        ocr_pdf_render.render_ocr_pdf_page(pdf_bytes())
    assert error.value.code == "OCR_PDF_RENDER_UNAVAILABLE"
    assert "poppler-utils" in str(error.value)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (subprocess.TimeoutExpired("pdftoppm", 30, stderr=b"private-document-body"), "OCR_PDF_RENDER_TIMEOUT"),
        (subprocess.CalledProcessError(1, "pdftoppm", stderr=b"private-document-body"), "OCR_PDF_RENDER_FAILED"),
        (OSError("private-document-body"), "OCR_PDF_RENDER_FAILED"),
    ],
)
def test_pdf_render_failure_is_safe_and_cleans_up(monkeypatch, failure, code) -> None:
    paths = []

    def run(args, **_kwargs):
        paths.append(Path(args[-2]))
        raise failure

    monkeypatch.setattr(ocr_pdf_render.shutil, "which", lambda _: "/usr/bin/pdftoppm")
    monkeypatch.setattr(ocr_pdf_render.subprocess, "run", run)
    with pytest.raises(OCRServiceError) as error:
        ocr_pdf_render.render_ocr_pdf_page(pdf_bytes())
    assert error.value.code == code
    assert "private-document-body" not in str(error.value)
    assert paths and not paths[0].parent.exists()


@pytest.mark.parametrize(
    ("output", "code"),
    [
        (None, "OCR_PDF_RENDER_FAILED"),
        (b"not a PNG", "OCR_PDF_RENDER_INVALID"),
        (PNG[:16] + struct.pack(">II", 2049, 10) + PNG[24:], "OCR_PDF_RENDER_INVALID"),
        (PNG[:16] + struct.pack(">II", 0, 10) + PNG[24:], "OCR_PDF_RENDER_INVALID"),
    ],
)
def test_pdf_render_validates_output(monkeypatch, output, code) -> None:
    def run(args, **_kwargs):
        if output is not None:
            Path(args[-1]).with_suffix(".png").write_bytes(output)

    monkeypatch.setattr(ocr_pdf_render.shutil, "which", lambda _: "/usr/bin/pdftoppm")
    monkeypatch.setattr(ocr_pdf_render.subprocess, "run", run)
    with pytest.raises(OCRServiceError) as error:
        ocr_pdf_render.render_ocr_pdf_page(pdf_bytes())
    assert error.value.code == code


def test_pdf_render_limits_output_bytes(monkeypatch) -> None:
    def run(args, **_kwargs):
        Path(args[-1]).with_suffix(".png").write_bytes(PNG)

    monkeypatch.setattr(ocr_pdf_render, "MAX_PNG_BYTES", 10)
    monkeypatch.setattr(ocr_pdf_render.shutil, "which", lambda _: "/usr/bin/pdftoppm")
    monkeypatch.setattr(ocr_pdf_render.subprocess, "run", run)
    with pytest.raises(OCRServiceError) as error:
        ocr_pdf_render.render_ocr_pdf_page(pdf_bytes())
    assert error.value.code == "OCR_PDF_RENDER_TOO_LARGE"


@pytest.mark.skipif(shutil.which("pdftoppm") is None, reason="poppler-utils is not installed")
def test_pdf_render_real_blank_page() -> None:
    result = ocr_pdf_render.render_ocr_pdf_page(pdf_bytes())
    assert result.startswith(ocr_pdf_render.PNG_SIGNATURE)
    width, height = struct.unpack(">II", result[16:24])
    assert 0 < width <= 2048
    assert height == 2048
