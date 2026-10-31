"""
rececionista/transport/__init__.py — Canais de envio.

`simulator`  : não envia nada (a GUI mostra a resposta) — para testes rápidos.
`whatsapp_web`: envia por WhatsApp Web, via Playwright, no Chrome do utilizador.
"""

from .base import WhatsAppTransport
from .simulator import SimulatorTransport

__all__ = ["WhatsAppTransport", "SimulatorTransport"]
