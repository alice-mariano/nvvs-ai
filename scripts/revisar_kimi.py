"""Envia os artefatos de um portão de revisão para a Kimi pela API e salva a resposta (ADR-011).

A Kimi recebe só o briefing fixo (docs/reviews/briefing.md), os documentos de contexto que vão
sempre (docs/contexto.md e docs/documentacao-v1.md) e os arquivos listados, sem nenhuma
explicação ou transcrição do Claude. Cada execução é uma conversa nova.

Uso (na raiz do projeto, com a venv ativa):
    python scripts/revisar_kimi.py R1 sql/001_schema.sql src/nvvs/collectors/base.py ...
    python scripts/revisar_kimi.py R1 --replica docs/reviews/R1-replica-pedido.md
    python scripts/revisar_kimi.py --listar-modelos

A chave fica no .env (MOONSHOT_API_KEY=...), nunca no código nem no Git.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
REVIEWS = ROOT / "docs" / "reviews"
BRIEFING = REVIEWS / "briefing.md"
BLOCKED_NAMES = {".env"}
# Vão em toda revisão, antes dos artefatos do portão: não depende de lembrar de listar
# (lição do R1-03 e do R2-03). Em conflito entre os dois, vale o contexto.md.
ALWAYS_SENT = ["docs/contexto.md", "docs/documentacao-v1.md"]


class KimiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    moonshot_api_key: SecretStr
    moonshot_base_url: str = "https://api.moonshot.ai/v1"
    moonshot_model: str = "kimi-k3.1-pro"


def _client(settings: KimiSettings) -> httpx.Client:
    return httpx.Client(
        base_url=settings.moonshot_base_url,
        headers={"Authorization": f"Bearer {settings.moonshot_api_key.get_secret_value()}"},
        # Com streaming, o limite de leitura vale entre dois pedaços da resposta,
        # e não para a resposta inteira: o raciocínio pode durar vários minutos.
        timeout=httpx.Timeout(300, connect=30),
    )


def _read_artifact(path_str: str) -> tuple[str, str]:
    path = (ROOT / path_str).resolve()
    if ROOT not in path.parents:
        sys.exit(f"Recusado: {path_str} está fora do projeto.")
    if path.name in BLOCKED_NAMES or path.name.startswith(".env."):
        sys.exit(f"Recusado: {path_str} pode conter segredos e nunca é enviado.")
    if not path.is_file():
        sys.exit(f"Arquivo não encontrado: {path_str}")
    return path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")


def _artifacts_message(gate: str, files: list[str]) -> str:
    parts = [f"Portão {gate}. Artefatos para revisão:"]
    for name, text in (_read_artifact(f) for f in files):
        parts.append(f"\n### `{name}`\n\n````\n{text}\n````")
    return "\n".join(parts)


def _chat(settings: KimiSettings, model: str, messages: list[dict]) -> dict:
    """Pede a resposta em streaming: os pedaços chegam enquanto a Kimi raciocina e escreve.

    Sem streaming, a API só responde quando termina tudo, e uma revisão longa estoura o tempo
    de espera da conexão sem nenhum byte recebido.
    """
    payload = {"model": model, "messages": messages, "stream": True}
    content: list[str] = []
    reasoning_chars = 0
    usage: dict = {}
    model_used = model
    last_report = 0

    with _client(settings) as client, client.stream("POST", "/chat/completions", json=payload) as r:
        if r.status_code >= 400:
            sys.exit(f"Erro {r.status_code} da API: {r.read().decode(errors='replace')[:2000]}")
        for line in r.iter_lines():
            if not line.startswith("data:"):
                continue
            data = line.removeprefix("data:").strip()
            if data == "[DONE]":
                break
            chunk = json.loads(data)
            model_used = chunk.get("model", model_used)
            usage = chunk.get("usage") or usage
            for choice in chunk.get("choices", []):
                usage = choice.get("usage") or usage
                delta = choice.get("delta", {})
                reasoning_chars += len(delta.get("reasoning_content") or "")
                content.append(delta.get("content") or "")
            written = sum(len(c) for c in content)
            if (
                reasoning_chars + written - last_report >= 2000
            ):  # sinal de vida a cada ~2 mil caracteres
                last_report = reasoning_chars + written
                print(
                    f"  raciocínio: {reasoning_chars} caracteres | resposta: {written} caracteres"
                )

    return {"model": model_used, "content": "".join(content), "usage": usage}


def _save(out: Path, gate: str, model: str, files: list[str], data: dict) -> None:
    usage = data.get("usage", {})
    content = data["content"]
    if not content.strip():
        sys.exit("A resposta veio vazia; nada foi salvo. Rode de novo.")
    header = [
        f"# {gate}: resposta da Kimi",
        "",
        f"- Modelo: `{data.get('model', model)}`",
        f"- Data (UTC): {datetime.now(UTC):%Y-%m-%d %H:%M}",
        (
            f"- Tokens: entrada {usage.get('prompt_tokens', '?')}, "
            f"saída {usage.get('completion_tokens', '?')}"
        ),
    ]
    if files:
        header.append("- Artefatos: " + ", ".join(f"`{f}`" for f in files))
    out.write_text("\n".join(header) + "\n\n---\n\n" + content + "\n", encoding="utf-8")
    print(f"Resposta salva em {out.relative_to(ROOT)}")
    print(f"Tokens: entrada {usage.get('prompt_tokens')}, saída {usage.get('completion_tokens')}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Revisão independente pela Kimi (ADR-011).")
    parser.add_argument("portao", nargs="?", help="Portão de revisão, por exemplo R1")
    parser.add_argument("arquivos", nargs="*", help="Artefatos a enviar, caminhos a partir da raiz")
    parser.add_argument("--replica", help="Arquivo .md com a réplica (no máximo uma por portão)")
    parser.add_argument("--modelo", help="Modelo da Kimi (padrão: MOONSHOT_MODEL ou kimi-k3.1-pro)")
    parser.add_argument("--listar-modelos", action="store_true", help="Mostra os modelos da conta")
    args = parser.parse_args()

    settings = KimiSettings()
    model = args.modelo or settings.moonshot_model

    if args.listar_modelos:
        with _client(settings) as client:
            response = client.get("/models")
        response.raise_for_status()
        for item in response.json().get("data", []):
            print(item.get("id"))
        return

    if not args.portao:
        parser.error("informe o portão, por exemplo R1")
    if not BRIEFING.is_file():
        sys.exit(f"Falta o briefing fixo em {BRIEFING.relative_to(ROOT)}")
    briefing = BRIEFING.read_text(encoding="utf-8")
    gate = args.portao.upper()
    first_out = REVIEWS / f"{gate}-kimi.md"

    if args.replica:
        if not first_out.is_file():
            sys.exit(f"Rode a revisão inicial antes: falta {first_out.relative_to(ROOT)}")
        saved = first_out.read_text(encoding="utf-8")
        files = [
            f.strip(" `") for f in saved.split("- Artefatos: ", 1)[1].split("\n", 1)[0].split(",")
        ]
        previous = saved.split("\n---\n", 1)[1].strip()
        replica = Path(args.replica).read_text(encoding="utf-8")
        messages = [
            {"role": "system", "content": briefing},
            {"role": "user", "content": _artifacts_message(gate, files)},
            {"role": "assistant", "content": previous},
            {"role": "user", "content": replica},
        ]
        out = REVIEWS / f"{gate}-kimi-replica.md"
        if out.exists():
            sys.exit(
                f"Já existe {out.relative_to(ROOT)}: o protocolo permite uma réplica por portão."
            )
    else:
        if not args.arquivos:
            parser.error("liste os artefatos a enviar")
        files = ALWAYS_SENT + [f for f in args.arquivos if f not in ALWAYS_SENT]
        messages = [
            {"role": "system", "content": briefing},
            {"role": "user", "content": _artifacts_message(gate, files)},
        ]
        out = first_out
        if out.exists():
            sys.exit(f"Já existe {out.relative_to(ROOT)}. Apague-o se quiser refazer a revisão.")

    print(f"Enviando {gate} para {model}. O raciocínio pode levar alguns minutos...")
    data = _chat(settings, model, messages)
    _save(out, gate, model, files, data)


if __name__ == "__main__":
    main()
