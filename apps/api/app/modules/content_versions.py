"""资料文字版本和文本块的共享构造逻辑。"""
import re
from collections.abc import Sequence

from app.models import Asset, AssetVersion, TextChunk
from app.modules.parsers import SourceBlock


def _append_chunk(
    chunks: list[TextChunk],
    version: AssetVersion,
    piece: str,
    start: int,
    *,
    block_type: str = "TEXT",
    locator: dict | None = None,
) -> None:
    chunks.append(
        TextChunk(
            asset_version=version,
            ordinal=len(chunks),
            content=piece,
            start_offset=start,
            end_offset=start + len(piece),
            block_type=block_type,
            locator=locator or {},
        )
    )


def build_chunks(
    content: str,
    version: AssetVersion,
    source_blocks: Sequence[SourceBlock] | None = None,
) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    if source_blocks:
        search_from = 0
        for block in source_blocks:
            if not block.content:
                continue
            block_start = content.find(block.content, search_from)
            if block_start < 0:
                block_start = search_from
            for part, relative_start in enumerate(range(0, len(block.content), 4000), start=1):
                piece = block.content[relative_start : relative_start + 4000]
                locator = dict(block.locator)
                if len(block.content) > 4000:
                    locator["part"] = part
                _append_chunk(
                    chunks,
                    version,
                    piece,
                    block_start + relative_start,
                    block_type=block.block_type,
                    locator=locator,
                )
            search_from = block_start + len(block.content)
        if chunks:
            return chunks

    for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", content, re.DOTALL):
        block = match.group(0)
        for relative_start in range(0, len(block), 4000):
            piece = block[relative_start : relative_start + 4000]
            start = match.start() + relative_start
            _append_chunk(chunks, version, piece, start)
    if not chunks:
        _append_chunk(chunks, version, content, 0)
    return chunks


def create_asset_version(
    asset: Asset,
    content: str,
    source_blocks: Sequence[SourceBlock] | None = None,
) -> AssetVersion:
    asset.current_version += 1
    version = AssetVersion(asset=asset, version=asset.current_version, content=content)
    build_chunks(content, version, source_blocks)
    return version
