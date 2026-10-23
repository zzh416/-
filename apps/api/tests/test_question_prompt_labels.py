import pytest

from app.modules.question_extraction_service import question_prompt_from_quote


@pytest.mark.parametrize('quote,expected', [
    ('什么是缓存？ 参考答案：', '什么是缓存？'),
    ('### 14. 面试官：Agent 和 Chain 的区别？\n**参考答案：**', '14. 面试官：Agent 和 Chain 的区别？'),
    ('有哪些框架？简单介绍。参考答案：', '有哪些框架？简单介绍。'),
    ('如何实现缓存？ __答案：__', '如何实现缓存？'),
])
def test_answer_heading_after_question_is_not_part_of_prompt(quote, expected):
    assert question_prompt_from_quote(quote) == expected


@pytest.mark.parametrize('quote', [
    '请解释“参考答案”的含义。',
    '如何展示参考答案：',
    '什么是缓存？参考答案：缓存是临时存储。',
])
def test_label_cleanup_does_not_remove_meaningful_content(quote):
    assert question_prompt_from_quote(quote) == quote
