"""
rececionista/errors.py — Falhas de gravação que não podem passar em silêncio.

Quando uma escrita na base de dados falha (ficheiro bloqueado, disco cheio,
base corrompida), a conversa tem de continuar, mas alguém tem de ver o erro.
Este módulo guarda um pequeno histórico em memória que a interface drena e
mostra no separador **Registos** — funciona mesmo quando a base é ela própria
o problema.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime

log = logging.getLogger("rececionista.errors")

_LOCK = threading.Lock()
_BUFFER: deque[tuple[datetime, str, str]] = deque(maxlen=100)


def report(module: str, message: str) -> None:
    """Regista uma falha para a interface mostrar (não levanta exceções)."""
    entry = (datetime.now(), str(module), str(message))
    with _LOCK:
        _BUFFER.append(entry)
    log.error("[%s] %s", module, message)


def drain() -> list[tuple[datetime, str, str]]:
    """Devolve e limpa as falhas acumuladas desde a última leitura."""
    with _LOCK:
        items = list(_BUFFER)
        _BUFFER.clear()
    return items


def clear() -> None:
    with _LOCK:
        _BUFFER.clear()
