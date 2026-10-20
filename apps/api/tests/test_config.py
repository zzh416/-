"""配置边界测试。"""
from sqlalchemy.engine import URL

from app.config import Settings


def test_database_url_preserves_special_characters_in_credentials() -> None:
    settings = Settings(
        _env_file=None,
        database_url=None,
        postgres_host="postgres",
        postgres_port=5432,
        postgres_db="bagu",
        postgres_user="bagu@example.com",
        postgres_password="demo@value:/#?",
    )

    url = settings.sqlalchemy_database_url

    assert isinstance(url, URL)
    assert url.host == "postgres"
    assert url.username == "bagu@example.com"
    assert url.password == "demo@value:/#?"


def test_explicit_database_url_takes_precedence() -> None:
    settings = Settings(
        _env_file=None,
        database_url="sqlite+pysqlite:///:memory:",
        postgres_host="ignored",
    )

    assert settings.sqlalchemy_database_url.drivername == "sqlite+pysqlite"
    assert settings.sqlalchemy_database_url.database == ":memory:"
