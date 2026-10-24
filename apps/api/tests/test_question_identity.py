import pytest

from app.modules.question_identity import question_identity, question_similarity


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("1. 什么是 GIL？", "什么是GIL?"),
        ("## 2、什么是 GIL", "什么是 GIL。"),
        ("1.2) **什么是 `GIL`？**", "什么是 gil"),
    ],
)
def test_question_identity_merges_formatting_only_differences(
    left: str, right: str
) -> None:
    assert question_identity(left) == question_identity(right)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("Java 是什么？", "JavaScript 是什么？"),
        ("索引是什么？", "什么是索引？"),
        ("线程和进程有什么区别？", "线程是什么？"),
    ],
)
def test_question_identity_keeps_semantically_different_questions_separate(
    left: str, right: str
) -> None:
    assert question_identity(left) != question_identity(right)


def test_question_similarity_surfaces_near_duplicate_without_auto_merging() -> None:
    assert question_similarity(
        "Python 中的 GIL 是什么？", "Python 的 GIL 是什么作用？"
    ) >= 0.8
    assert question_similarity("索引是什么？", "什么是索引？") < 0.8
