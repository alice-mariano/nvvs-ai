"""Configuração da conexão (08/11: localhost no Windows custava ~130 s por conexão)."""

from nvvs.config import Settings


def test_default_host_is_ipv4_and_connection_has_timeout(monkeypatch):
    monkeypatch.delenv("POSTGRES_HOST", raising=False)
    settings = Settings(_env_file=None, postgres_user="u", postgres_password="p", postgres_db="d")
    assert settings.postgres_host == "127.0.0.1"
    assert "host=127.0.0.1" in settings.conninfo
    assert "connect_timeout=10" in settings.conninfo
