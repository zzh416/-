from app.modules.question_miss_detection import (
    CoveredQuestionRange,
    MissDetectionBlock,
    find_suspected_questions,
)


def test_miss_detection_finds_question_styles_and_ignores_urls() -> None:
    content = (
        "### 1.1 索引优化\n"
        "为什么联合索引要遵守最左匹配？\n"
        "文档地址：https://example.test/search?q=why?\n"
        "这是普通说明文字。\n"
    )

    misses = find_suspected_questions(
        [MissDetectionBlock(key="a", ordinal=0, content=content, locator={"page": 1})],
        [],
    )

    assert [(item.quote, item.reason) for item in misses] == [
        ("### 1.1 索引优化", "HEADING"),
        ("为什么联合索引要遵守最左匹配？", "QUESTION_MARK"),
    ]


def test_miss_detection_excludes_ranges_already_extracted() -> None:
    content = "什么是事件循环？\n事件循环负责调度任务。\n"
    question_end = content.index("\n")

    misses = find_suspected_questions(
        [MissDetectionBlock(key="a", ordinal=0, content=content, locator={})],
        [CoveredQuestionRange(block_key="a", start=0, end=question_end)],
    )

    assert misses == []


def test_miss_detection_catches_spoken_interview_instruction() -> None:
    misses = find_suspected_questions(
        [
            MissDetectionBlock(
                key="a",
                ordinal=0,
                content="先介绍一下上一段的实习\n",
                locator={},
            )
        ],
        [],
    )

    assert [(item.quote, item.reason) for item in misses] == [
        ("先介绍一下上一段的实习", "QUESTION_PATTERN")
    ]
