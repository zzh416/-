"""应用配置：从环境变量 / .env 读取。"""
from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url


class Settings(BaseSettings):
    """集中管理配置项。

    字段名与 `docker-compose.yml` 中的环境变量一一对应，
    也支持通过 `.env` 文件覆盖（用于本地开发）。
    """

    app_name: str = "面经综合助手"
    environment: str = "development"
    log_level: str = "INFO"

    # 数据库与缓存
    database_url: str | None = None
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "bagu"
    postgres_user: str = "bagu"
    postgres_password: str = Field(default="bagu", repr=False)
    redis_url: str = "redis://redis:6379/0"

    # 原文件存储目录（容器内路径，由 Compose 挂载到宿主机 data/）
    data_dir: str = "/data"
    max_text_import_bytes: int = 1024 * 1024
    max_file_import_bytes: int = 25 * 1024 * 1024
    max_ocr_image_bytes: int = 10 * 1024 * 1024
    max_extracted_text_bytes: int = 5 * 1024 * 1024
    max_url_response_bytes: int = 5 * 1024 * 1024
    max_pdf_pages: int = 100
    max_pdf_content_stream_bytes: int = 32 * 1024 * 1024
    ingestion_http_timeout_seconds: float = 20.0
    ingestion_retry_limit: int = 3
    renderer_url: str = "http://renderer:9100"
    renderer_timeout_seconds: float = 30.0

    # 模型网关 —— 默认 GLM，DeepSeek 降级
    glm_api_key: str = Field(default="", repr=False)
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    glm_text_model: str = "glm-4-plus"
    glm_ocr_model: str = "glm-ocr"

    deepseek_api_key: str = Field(default="", repr=False)
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_text_model: str = "deepseek-chat"

    # Embedding —— SiliconFlow BGE-M3
    embedding_api_key: str = Field(default="", repr=False)
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_model: str = "BAAI/bge-m3"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @computed_field(repr=False)
    @property
    def sqlalchemy_database_url(self) -> URL:
        """返回不会因账号或密码中的保留字符而歧义的数据库 URL。"""
        if self.database_url:
            return make_url(self.database_url)

        return URL.create(
            drivername="postgresql+psycopg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=self.postgres_host,
            port=self.postgres_port,
            database=self.postgres_db,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
