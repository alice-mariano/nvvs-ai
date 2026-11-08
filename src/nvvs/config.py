"""Configuração lida do arquivo .env (nunca do código)."""

from functools import lru_cache

from psycopg.conninfo import make_conninfo
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    postgres_user: str
    postgres_password: str
    postgres_db: str
    # 127.0.0.1 e não "localhost": no Windows, "localhost" tenta primeiro o IPv6 (::1), onde o
    # Docker não publica a porta, e cada conexão esperava ~130 s antes de cair no IPv4 (08/11).
    postgres_host: str = "127.0.0.1"
    # Segundos por tentativa de conexão: um banco fora do ar falha com mensagem, não trava
    postgres_connect_timeout_s: int = 10
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
            connect_timeout=self.postgres_connect_timeout_s,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
