"""Configuração lida do arquivo .env (nunca do código)."""

from functools import lru_cache

from psycopg.conninfo import make_conninfo
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_host: str = "localhost"
    postgres_port: int = 5433

    nvvs_user_agent: str = "nvvs.ai/0.1"
    # Pausa entre requisições à mesma API, em segundos (RNF02)
    request_pause_s: float = 1.0

    @property
    def conninfo(self) -> str:
        # make_conninfo escapa a senha corretamente, mesmo com caracteres especiais
        return make_conninfo(
            host=self.postgres_host,
            port=self.postgres_port,
            dbname=self.postgres_db,
            user=self.postgres_user,
            password=self.postgres_password,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
