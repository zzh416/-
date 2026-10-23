from uuid import uuid4

import pytest

from app.modules.question_extraction_service import LoadedChunk, _build_extraction_batches


def _chunks(contents: list[str]) -> list[LoadedChunk]:
    return [LoadedChunk(id=uuid4(), ordinal=i, content=text) for i, text in enumerate(contents)]


def test_question_and_multiblock_answer_stay_together_at_block_limit() -> None:
    chunks = _chunks([
        "什么是 Chunk？", "答：文本片段。",
        "8. 什么是 Top-K？怎么选？", "答：最相关的 K 条结果。",
        "K 太大，噪声多。", "K 太小，信息不足。",
        "15. Agent 的执行流程？", "答：思考、执行、观察。",
    ])
    batches = _build_extraction_batches(chunks, max_chars=1000, max_blocks=5, overlap_blocks=1)

    assert any(all(chunk in batch for chunk in chunks[2:6]) for batch in batches)
    assert any(all(chunk in batch for chunk in chunks[6:8]) for batch in batches)
    assert {chunk.ordinal for batch in batches for chunk in batch} == set(range(8))
    assert all(len(batch) <= 5 for batch in batches)
    assert all(left[-1] == right[0] for left, right in zip(batches, batches[1:]))


def test_question_boundary_respects_character_limit() -> None:
    chunks = _chunks(["什么是缓存？", "答：保存结果。", "什么是检索？", "答：查找资料。", "提高召回。"])
    limit = sum(len(chunk.content) for chunk in chunks[:4])
    batches = _build_extraction_batches(chunks, max_chars=limit, max_blocks=20, overlap_blocks=1)

    assert any(all(chunk in batch for chunk in chunks[2:]) for batch in batches)
    assert all(sum(len(chunk.content) for chunk in batch) <= limit for batch in batches)


@pytest.mark.parametrize("answer", [
    "答案：为什么失败？这是输入有误。",
    "1. 判断输入中是否包含 '?'，再继续处理。",
    "1. 判断是否成功？",
    "比如用户会问：为什么失败？",
    "问题：K 太大会怎样？应减少数量。",
])
def test_answer_questions_and_numbered_steps_are_not_boundaries(answer: str) -> None:
    chunks = _chunks(["什么是检索？", "答：", answer, "使用向量。", "返回结果。"])
    batches = _build_extraction_batches(chunks, max_chars=1000, max_blocks=4, overlap_blocks=1)

    assert [[chunk.ordinal for chunk in batch] for batch in batches] == [[0, 1, 2, 3], [3, 4]]


@pytest.mark.parametrize("prompt", [
    "### **8. 什么是 Top-K？怎么选？**",
    "⭐ 如果让你设计一个 RAG 系统，你怎么做？",
    "请描述 RAG 的完整 Pipeline。",
    "先介绍一下上一段的实习",
    "2. 面试官：为什么需要 RAG？ 参考答案：提供外部知识。",
])
def test_explicit_question_styles_can_start_a_new_batch(prompt: str) -> None:
    chunks = _chunks(["什么是缓存？", "答：保存结果。", prompt, "后续答案。", "答案结尾。"])
    batches = _build_extraction_batches(chunks, max_chars=1000, max_blocks=4, overlap_blocks=1)

    assert [chunk.ordinal for chunk in batches[0]] == [0, 1]
    assert any(all(chunk in batch for chunk in chunks[2:]) for batch in batches)


def test_already_complete_boundary_does_not_shorten_batch() -> None:
    chunks = _chunks(["什么是缓存？", "答：保存结果。", "什么是检索？", "答：查找资料。"])
    batches = _build_extraction_batches(chunks, max_chars=1000, max_blocks=2, overlap_blocks=0)
    assert [[chunk.ordinal for chunk in batch] for batch in batches] == [[0, 1], [2, 3]]


def test_unstructured_source_and_oversized_question_keep_existing_fallback() -> None:
    chunks = _chunks(["x" * 10 for _ in range(7)])
    batches = _build_extraction_batches(chunks, max_chars=100, max_blocks=3, overlap_blocks=1)
    assert [[chunk.ordinal for chunk in batch] for batch in batches] == [[0, 1, 2], [2, 3, 4], [4, 5, 6]]

    chunks = _chunks(["什么是检索？"] + ["答案片段。"] * 6)
    batches = _build_extraction_batches(chunks, max_chars=100, max_blocks=3, overlap_blocks=1)
    assert [[chunk.ordinal for chunk in batch] for batch in batches] == [[0, 1, 2], [2, 3, 4], [4, 5, 6]]


@pytest.mark.parametrize("overlap", [0, 1, 2, 20])
@pytest.mark.parametrize("limit", [1, 2, 3, 4])
def test_batches_always_advance_cover_all_chunks_and_preserve_order(overlap: int, limit: int) -> None:
    chunks = _chunks(["什么是缓存？", "答：", "结果。", "什么是检索？", "答：", "资料。", "请介绍 RAG。", "解释。"])
    batches = _build_extraction_batches(chunks, max_chars=40, max_blocks=limit, overlap_blocks=overlap)
    assert len(batches) <= len(chunks)
    assert {chunk.ordinal for batch in batches for chunk in batch} == set(range(len(chunks)))
    assert all(len(batch) <= limit for batch in batches)
    assert all(batch == sorted(batch, key=lambda chunk: chunk.ordinal) for batch in batches)
    assert all(left[-1].ordinal < right[-1].ordinal for left, right in zip(batches, batches[1:]))


def test_single_oversized_chunk_is_not_dropped_or_split() -> None:
    chunks = _chunks(["什么是检索？" + "答案。" * 50, "另一段。"])
    batches = _build_extraction_batches(chunks, max_chars=30, max_blocks=3, overlap_blocks=1)
    assert batches == [[chunks[0]], [chunks[1]]]


def test_oversized_next_chunk_does_not_create_an_overlap_only_batch() -> None:
    chunks = _chunks(["什么是缓存？", "答：", "x" * 110])
    batches = _build_extraction_batches(chunks, max_chars=89, max_blocks=5, overlap_blocks=1)
    assert batches == [chunks[:2], chunks[2:]]
