"""Run pytest against a disposable database and storage directory."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path.cwd()))

from sqlalchemy import create_engine
from app.config import settings


def main() -> int:
    name = "bagu_acceptance_" + uuid4().hex
    url = settings.sqlalchemy_database_url
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", hide_parameters=True)
    created = False
    try:
        with admin.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{name}"')
            created = True
        with tempfile.TemporaryDirectory(prefix=name) as directory:
            env = dict(os.environ, DATABASE_URL=url.set(database=name).render_as_string(hide_password=False),
                       DATA_DIR=directory, TEXT_MODEL_PROVIDER="glm", OCR_PROVIDER="glm",
                       GLM_API_KEY="", DEEPSEEK_API_KEY="", EMBEDDING_API_KEY="")
            subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], env=env, check=True)
            return subprocess.run([sys.executable, "-m", "pytest", *(sys.argv[1:] or ["-q"])], env=env).returncode
    finally:
        if created:
            with admin.connect() as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
            print("Isolated acceptance database removed.", flush=True)
        admin.dispose()


if __name__ == "__main__":
    sys.exit(main())
