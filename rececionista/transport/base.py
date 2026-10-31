"""rececionista/transport/base.py — Interface abstracta de canal."""

from __future__ import annotations

from abc import ABC, abstractmethod


class WhatsAppTransport(ABC):
    """Contrato mínimo: enviar texto, enviar imagens, saber o estado."""

    @abstractmethod
    def send_text(self, chat: str, text: str) -> bool: ...

    @abstractmethod
    def send_images(self, chat: str, images: list[str], caption: str = "") -> bool: ...

    @abstractmethod
    def status(self) -> str: ...

    def close(self) -> None:  # pragma: no cover - opcional
        return None
