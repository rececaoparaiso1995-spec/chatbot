"""rececionista/transport/simulator.py — Canal nulo, usado pelo simulador da GUI."""

from __future__ import annotations

from .base import WhatsAppTransport


class SimulatorTransport(WhatsAppTransport):
    """
    Não envia nada para fora: as respostas ficam apenas na GUI.
    Permite testar o motor sem tocar em WhatsApp nem em Chrome.
    """

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, chat: str, text: str) -> bool:
        self.sent.append((chat, text))
        return True

    def send_images(self, chat: str, images: list[str], caption: str = "") -> bool:
        self.sent.append((chat, caption or f"[{len(images)} imagem(ns)]"))
        return True

    def status(self) -> str:
        return "simulador (nada é enviado)"
