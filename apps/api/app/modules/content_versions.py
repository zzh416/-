"""资料文字版本和文本块的共享构造逻辑。"""
import re

from app.models import Asset, AssetVersion, TextChunk


def build_chunks(content: str, version: AssetVersion) -> list[TextChunk]:
    chunks: list[TextChunk] = []
    ordinal = 0
    for match in re.finditer(r"\S(?:.*?\S)?(?=\n\s*\n|\Z)", content, re.DOTALL):
        block = match.group(0)
        for relative_start in range(0, len(block), 4000):
            piece = block[relative_start : relative_start + 4000]
            start = match.start() + relative_start
            chunks.append(
                TextChunk(
                    asset_version=version,
                    ordinal=ordinal,
                    content=piece,
                    start_offset=start,
                    end_offset=start + len(piece),
                )
            )
            ordinal += 1
    if not chunks:
        chunks.append(
            TextChunk(
                asset_version=version,
                ordinal=0,
                content=content,
                start_offset=0,
                end_offset=len(content),
            )
        )
    return chunks


def create_asset_version(asset: Asset, content: str) -> AssetVersion:
    asset.current_version += 1
    version = AssetVersion(asset=asset, version=asset.current_version, content=content)
    build_chunks(content, version)
    return version
