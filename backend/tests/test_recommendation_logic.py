from app.modules.recommendation_service import (
    PreferenceSnapshot,
    SkillSnapshot,
    build_recommendation,
    detect_technologies,
)


def test_detect_technologies_distinguishes_java_from_javascript_and_adds_parents() -> None:
    javascript = detect_technologies("JavaScript 的事件循环是什么？")
    assert [item.canonical_name for item in javascript] == ["javascript"]

    spring = detect_technologies("Spring Boot 的依赖注入如何实现？")
    assert {item.canonical_name for item in spring} == {"spring", "java"}

    assert [item.canonical_name for item in detect_technologies("智能指针如何避免内存泄漏？")] == [
        "cpp"
    ]
    assert [item.canonical_name for item in detect_technologies("HashMap 的扩容机制是什么？")] == [
        "java"
    ]
    assert {item.canonical_name for item in detect_technologies("InnoDB 的 MVCC 如何实现？")} == {
        "database",
        "mysql",
    }


def test_confirmed_python_profile_excludes_java_question_but_keeps_general_question() -> None:
    skills = [SkillSnapshot("python", "Python", "LANGUAGE", "CONFIRMED")]

    java_result = build_recommendation(
        prompt="Java 的 HashMap 为什么线程不安全？",
        answer=None,
        skills=skills,
        rules=[],
    )
    assert java_result.decision == "EXCLUDED"
    assert "QUESTION_LANGUAGE_MISMATCH" in java_result.reason_codes

    general_result = build_recommendation(
        prompt="数据库事务的 ACID 是什么？",
        answer=None,
        skills=skills,
        rules=[],
    )
    assert general_result.decision == "INCLUDED"


def test_incompatible_answer_does_not_remove_general_question() -> None:
    result = build_recommendation(
        prompt="如何避免并发修改导致的数据竞争？",
        answer="可以使用 Java synchronized 保护临界区。",
        skills=[SkillSnapshot("python", "Python", "LANGUAGE", "CONFIRMED")],
        rules=[],
    )

    assert result.decision == "INCLUDED"
    assert result.answer_state == "SOURCE_ANSWER"
    assert result.usable_answer_state == "MISSING"
    assert "ANSWER_LANGUAGE_MISMATCH" in result.reason_codes


def test_mysql_exclusion_does_not_exclude_database_general_knowledge() -> None:
    rule = PreferenceSnapshot("EXCLUDE", "mysql", "MySQL", "DATABASE_PRODUCT", "BOTH")

    mysql_result = build_recommendation(
        prompt="MySQL 的索引下推是什么？",
        answer="索引下推可以减少回表次数。",
        skills=[],
        rules=[rule],
    )
    assert mysql_result.decision == "EXCLUDED"
    assert "QUESTION_EXCLUDED_RULE" in mysql_result.reason_codes

    database_result = build_recommendation(
        prompt="数据库事务的隔离级别有哪些？",
        answer="常见有读未提交、读已提交、可重复读和串行化。",
        skills=[],
        rules=[rule],
    )
    assert database_result.decision == "INCLUDED"
    assert database_result.usable_answer_state == "ANSWERED"


def test_include_rule_increases_relevance_without_hiding_other_questions() -> None:
    python_rule = PreferenceSnapshot("INCLUDE", "python", "Python", "LANGUAGE", "BOTH")
    python_result = build_recommendation(
        prompt="Python 的生成器有什么优点？",
        answer=None,
        skills=[],
        rules=[python_rule],
    )
    neutral_result = build_recommendation(
        prompt="TCP 三次握手的过程是什么？",
        answer=None,
        skills=[],
        rules=[python_rule],
    )

    assert python_result.decision == "INCLUDED"
    assert neutral_result.decision == "INCLUDED"
    assert python_result.score > neutral_result.score
    assert "PREFERENCE_MATCH" in python_result.reason_codes


def test_framework_skill_does_not_claim_match_for_every_parent_language_question() -> None:
    result = build_recommendation(
        prompt="Python 的 GIL 是什么？",
        answer=None,
        skills=[SkillSnapshot("fastapi", "FastAPI", "FRAMEWORK", "CONFIRMED")],
        rules=[],
    )

    assert "PROFILE_SKILL_MATCH" not in result.reason_codes
    assert all("FastAPI" not in reason for reason in result.reasons)
