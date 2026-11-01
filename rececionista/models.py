"""
rececionista/models.py — Estruturas de dados do harness de teste.

Deliberadamente independentes de `database/models.py` (usado pelo módulo
real do PMS), para que a app de teste possa evoluir sem tocar no código
já validado da Fase 1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class VillaType:
    """Tipologia fictícia do hotel."""

    code: str
    name: str
    max_adults: int
    max_children: int
    base_rate: float

    def fits(self, adults: int, children: int) -> bool:
        return adults <= self.max_adults and children <= self.max_children


@dataclass(frozen=True)
class NightlyRate:
    """Preço de uma noite específica (pode variar de noite para noite)."""

    date: date
    price: float


@dataclass
class Offer:
    """Proposta de alojamento com preço por noite e total."""

    villa: VillaType
    meal: str                 # "BB" (pequeno-almoço) | "RO" (só alojamento)
    checkin: date
    nights: int
    adults: int
    children: int
    nightly: list[NightlyRate]
    total: float

    @property
    def checkout(self) -> date:
        from datetime import timedelta

        return self.checkin + timedelta(days=self.nights)

    @property
    def guests(self) -> int:
        return self.adults + self.children

    def nightly_json(self) -> str:
        import json

        return json.dumps(
            [{"date": n.date.isoformat(), "price": n.price} for n in self.nightly]
        )


@dataclass
class Outbound:
    """Mensagem a enviar ao cliente (texto ou imagens)."""

    kind: str = "text"                  # "text" | "image"
    text: str = ""
    images: list[str] = field(default_factory=list)
    phone: str = ""
    meta: dict = field(default_factory=dict)


@dataclass
class BookingDraft:
    """Pedido de reserva em recolha (nunca confirmado automaticamente)."""

    phone: str
    name: str | None = None
    email: str | None = None
    client_phone: str | None = None
    checkin: date | None = None
    checkout: date | None = None
    nights: int | None = None
    adults: int = 2
    children: int = 0
    villa: str | None = None
    meal: str = "BB"
    total: float | None = None
    nightly_json: str | None = None
    language: str = "pt"
    asked: int = 0

    def missing(self) -> list[str]:
        missing: list[str] = []
        if not self.name:
            missing.append("name")
        if not self.email:
            missing.append("email")
        if self.checkin is None:
            missing.append("dates")
        return missing

    def is_complete(self) -> bool:
        return not self.missing() and self.checkin is not None and self.villa is not None


@dataclass
class Session:
    """Estado em memória por número de WhatsApp."""

    phone: str
    language: str | None = None
    greeted: bool = False
    last_offer: Offer | None = None
    draft: BookingDraft | None = None
    want_booking: bool = False
    clarify_count: int = 0
    last_activity: datetime | None = None
    # Consulta interrompida por falta de noites: guarda o que o cliente já
    # disse (entrada, hóspedes, regime, villa) para a resposta seguinte
    # ("2") completar o pedido em vez de recomeçar do zero.
    pending_quote: dict | None = None
    # Pedido em recolha quando não há preços: datas, noites, hóspedes, regime
    # e nome são pedidos um a um antes de o pedido ser registado.
    request: dict | None = None


@dataclass
class BookingRecord:
    id: int | None = None
    phone: str = ""
    name: str = ""
    email: str = ""
    client_phone: str = ""
    checkin: str = ""
    checkout: str = ""
    nights: int = 0
    adults: int = 0
    children: int = 0
    villa: str = ""
    meal: str = ""
    total: float = 0.0
    nightly_json: str = ""
    status: str = "pending"
    created_at: str = ""
    updated_at: str = ""
