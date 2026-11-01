"""
rececionista/settings.py — Configuração persistente da app de teste.

Guardada em `data/teste_app/app_config.json` para não colidir com o
`config.py` do projeto principal (que aponta para `data/hotel_bot.db`).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "teste_app"
CONFIG_PATH = DATA_DIR / "app_config.json"
DB_PATH = DATA_DIR / "teste.db"
PHOTOS_DIR = DATA_DIR / "photos"
DOCS_DIR = DATA_DIR / "docs"

DATA_DIR.mkdir(parents=True, exist_ok=True)
PHOTOS_DIR.mkdir(parents=True, exist_ok=True)
DOCS_DIR.mkdir(parents=True, exist_ok=True)

MODE_AUTOMATICO = "AUTOMATICO"
MODE_TESTE = "TESTE"
MODE_DESLIGADO = "DESLIGADO"
MODE_EMERGENCIA = "EMERGENCIA"
MODES = (MODE_AUTOMATICO, MODE_TESTE, MODE_DESLIGADO, MODE_EMERGENCIA)

# Números fictícios usados no simulador (formato E.164 sem espaços).
DEFAULT_TEST_NUMBERS = ["+351900000001", "+351900000002"]


@dataclass
class AppConfig:
    """Parâmetros configuráveis pela interface."""

    mode: str = MODE_TESTE
    test_numbers: list[str] = field(default_factory=lambda: list(DEFAULT_TEST_NUMBERS))

    # Horário. Nesta build de teste o horário é IGNORADO por defeito,
    # para se poder validar respostas a qualquer hora do dia.
    respect_schedule: bool = False
    schedule_start: str = "22:30"
    schedule_end: str = "07:00"

    # Disponibilidade e preços.
    #   True  -> lê o ficheiro CSV indicado (sem navegador, sem Newhotel)
    #   False -> não consulta nada: regista o pedido e diz que a receção
    #            responde quando abrir
    use_availability_file: bool = False
    availability_file: str = str(DATA_DIR / "disponibilidade.csv")

    # Modelo local (Ollama) para compreender mensagens que as regras não
    # apanham. Só interpreta; preços e confirmações continuam nos templates.
    use_llm: bool = False
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:4b"
    ollama_timeout: int = 45

    debounce_seconds: int = 8
    send_acknowledgement: bool = True
    default_language: str = "pt"
    max_photos_per_send: int = 5

    # WhatsApp Web (Playwright)
    whatsapp_profile_dir: str = str(DATA_DIR / "chrome_profile_whatsapp")
    whatsapp_send_mode: str = "conversa_aberta"  # conversa_aberta | por_nome
    whatsapp_target: str = ""
    whatsapp_poll_seconds: int = 5
    whatsapp_auto_poll: bool = False

    # Encaminhar automaticamente as respostas do simulador para o WhatsApp Web.
    auto_forward: bool = False

    simulator_phone: str = "+351900000001"

    def save(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )

    @classmethod
    def load(cls) -> "AppConfig":
        if not CONFIG_PATH.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            raw: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls()
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in raw.items() if k in known})
