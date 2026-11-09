"""Mensagens da linha de comando."""

import psycopg
import pytest
from typer.testing import CliRunner

from nvvs import cli
from nvvs.config import get_settings


@pytest.fixture
def settings_without_env_file(monkeypatch):
    # O teste não pode depender do .env de quem roda: define o mínimo e limpa o cache
    for name, value in {"POSTGRES_USER": "u", "POSTGRES_PASSWORD": "p", "POSTGRES_DB": "d"}.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("POSTGRES_HOST", "127.0.0.1")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_database_down_gives_instructions_instead_of_traceback(
    monkeypatch, settings_without_env_file
):
    def fail():
        raise psycopg.errors.ConnectionTimeout("connection timeout expired")

    monkeypatch.setattr(cli, "connect", fail)
    result = CliRunner().invoke(cli.app, ["embed"])
    assert result.exit_code == 2
    assert "127.0.0.1" in result.output
    assert "docker compose start" in result.output
    assert "Traceback" not in result.output
