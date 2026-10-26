"""Migration acceptance tests run against an isolated temporary PostgreSQL database."""
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

from app.config import settings


API_ROOT = Path(__file__).resolve().parents[1]
P1_REVISION = "20261020_0002"


def _migration_config(database_url: str) -> Config:
    config = Config(str(API_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(API_ROOT / "migrations"))
    config.attributes["database_url"] = database_url
    return config


def _assert_p1_content(database_url: str, ids: dict[str, object]) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT prompt FROM questions WHERE id = :id"),
                {"id": ids["question"]},
            ).scalar_one() == "Python 的 GIL 是什么？"
            assert connection.execute(
                text("SELECT content FROM answers WHERE id = :id"),
                {"id": ids["answer"]},
            ).scalar_one() == "GIL 是 CPython 的全局解释器锁。"
            assert connection.execute(
                text("SELECT content FROM answer_revisions WHERE answer_id = :id"),
                {"id": ids["answer"]},
            ).scalar_one() == "GIL 是 CPython 的全局解释器锁。"
            assert connection.execute(
                text("SELECT content FROM question_notes WHERE question_id = :id"),
                {"id": ids["question"]},
            ).scalar_one() == "补充线程切换的例子"
            assert connection.execute(
                text("SELECT content FROM asset_versions WHERE id = :id"),
                {"id": ids["asset_version"]},
            ).scalar_one() == "旧版 P1 原文"
    finally:
        engine.dispose()


def test_p1_data_survives_upgrade_downgrade_and_reupgrade() -> None:
    database_name = f"bagu_migration_{uuid4().hex}"
    application_url = settings.sqlalchemy_database_url
    admin_url = application_url.set(database="postgres")
    temporary_url = application_url.set(database=database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    temporary_engine = None
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}"')

        rendered_url = temporary_url.render_as_string(hide_password=False)
        migration_config = _migration_config(rendered_url)
        command.upgrade(migration_config, P1_REVISION)

        ids = {
            "collection": uuid4(),
            "asset": uuid4(),
            "asset_version": uuid4(),
            "chunk": uuid4(),
            "question": uuid4(),
            "answer": uuid4(),
            "answer_revision": uuid4(),
            "note": uuid4(),
        }
        owner_id = "00000000-0000-0000-0000-000000000001"
        temporary_engine = create_engine(temporary_url)
        with temporary_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO collections (id, owner_id, name, purpose) "
                    "VALUES (:id, :owner_id, 'P1 资料库', 'KNOWLEDGE_BASE')"
                ),
                {"id": ids["collection"], "owner_id": owner_id},
            )
            connection.execute(
                text(
                    "INSERT INTO assets "
                    "(id, owner_id, collection_id, title, source_format, status, current_version) "
                    "VALUES (:id, :owner_id, :collection_id, 'P1 原始资料', "
                    "'PASTED_TEXT', 'READY', 1)"
                ),
                {
                    "id": ids["asset"],
                    "owner_id": owner_id,
                    "collection_id": ids["collection"],
                },
            )
            connection.execute(
                text(
                    "INSERT INTO asset_versions (id, asset_id, version, content) "
                    "VALUES (:id, :asset_id, 1, '旧版 P1 原文')"
                ),
                {"id": ids["asset_version"], "asset_id": ids["asset"]},
            )
            connection.execute(
                text(
                    "INSERT INTO text_chunks "
                    "(id, asset_version_id, ordinal, content, start_offset, end_offset) "
                    "VALUES (:id, :asset_version_id, 0, '旧版 P1 原文', 0, 8)"
                ),
                {"id": ids["chunk"], "asset_version_id": ids["asset_version"]},
            )
            connection.execute(
                text(
                    "INSERT INTO questions "
                    "(id, owner_id, collection_id, source_chunk_id, source_type, prompt, version) "
                    "VALUES (:id, :owner_id, :collection_id, :chunk_id, 'SOURCE', "
                    "'Python 的 GIL 是什么？', 1)"
                ),
                {
                    "id": ids["question"],
                    "owner_id": owner_id,
                    "collection_id": ids["collection"],
                    "chunk_id": ids["chunk"],
                },
            )
            connection.execute(
                text(
                    "INSERT INTO answers "
                    "(id, question_id, owner_id, content, source_type, version) "
                    "VALUES (:id, :question_id, :owner_id, "
                    "'GIL 是 CPython 的全局解释器锁。', 'USER_WRITTEN', 1)"
                ),
                {
                    "id": ids["answer"],
                    "question_id": ids["question"],
                    "owner_id": owner_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO answer_revisions (id, answer_id, version, content) "
                    "VALUES (:id, :answer_id, 1, 'GIL 是 CPython 的全局解释器锁。')"
                ),
                {"id": ids["answer_revision"], "answer_id": ids["answer"]},
            )
            connection.execute(
                text(
                    "INSERT INTO question_notes "
                    "(id, question_id, owner_id, content, version) "
                    "VALUES (:id, :question_id, :owner_id, '补充线程切换的例子', 1)"
                ),
                {
                    "id": ids["note"],
                    "question_id": ids["question"],
                    "owner_id": owner_id,
                },
            )

        command.upgrade(migration_config, "head")
        _assert_p1_content(rendered_url, ids)
        command.downgrade(migration_config, P1_REVISION)
        _assert_p1_content(rendered_url, ids)
        command.upgrade(migration_config, "head")
        _assert_p1_content(rendered_url, ids)
    finally:
        if temporary_engine is not None:
            temporary_engine.dispose()
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        admin_engine.dispose()
