"""
database/models.py — Modelos de dados (dataclasses) do Paraíso Bot
Define as estruturas de dados usadas pelo módulo PMS e pelos módulos futuros.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional
import json


# ---------------------------------------------------------------------------
# Modelos do Módulo PMS (Newhotel Cloud)
# ---------------------------------------------------------------------------

@dataclass
class NightlyRate:
    """
    Preço e disponibilidade para uma noite específica numa tipologia.
    """
    date: date          # Data da noite (ex: 2026-11-01)
    price: float        # Preço por noite em euros
    available: int      # Número de unidades disponíveis (0 = indisponível)

    @property
    def is_available(self) -> bool:
        """True se houver disponibilidade para esta noite."""
        return self.available > 0

    def to_dict(self) -> dict:
        return {
            "date": self.date.isoformat(),
            "price": self.price,
            "available": self.available,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "NightlyRate":
        return cls(
            date=date.fromisoformat(data["date"]),
            price=float(data["price"]),
            available=int(data["available"]),
        )


@dataclass
class TipologiaResult:
    """
    Resultado de disponibilidade e preços para uma tipologia de alojamento
    (T0, T1 ou T2) numa pesquisa.
    """
    code: str                          # "T0", "T1" ou "T2"
    nightly_rates: list[NightlyRate]   # Uma entrada por noite da estadia
    total_price: float                 # Preço total de todas as noites
    is_available: bool                 # False se qualquer noite estiver indisponível

    def get_unavailable_nights(self) -> list[NightlyRate]:
        """Retorna as noites com disponibilidade zero."""
        return [nr for nr in self.nightly_rates if not nr.is_available]

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "nightly_rates": [nr.to_dict() for nr in self.nightly_rates],
            "total_price": self.total_price,
            "is_available": self.is_available,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TipologiaResult":
        return cls(
            code=data["code"],
            nightly_rates=[NightlyRate.from_dict(nr) for nr in data["nightly_rates"]],
            total_price=float(data["total_price"]),
            is_available=bool(data["is_available"]),
        )


@dataclass
class PMSQueryResult:
    """
    Resultado completo de uma pesquisa de disponibilidade no Newhotel Cloud.
    Pode conter uma ou várias tipologias, consoante o filtro aplicado.
    """
    checkin: date                       # Data de chegada
    checkout: date                      # Data de saída (checkin + nights)
    nights: int                         # Número de noites
    pax: int                            # Número total de hóspedes
    pension: str                        # "BB" (c/ pequeno-almoço) ou "RO" (s/ refeições)
    tipologias: list[TipologiaResult]   # Resultados por tipologia
    screenshot_path: Optional[str]      # Caminho do screenshot de auditoria
    query_timestamp: str                # ISO timestamp da consulta
    success: bool                       # True se a pesquisa foi bem-sucedida
    error_message: Optional[str] = None # Mensagem de erro se success=False

    # ------------------------------------------------------------------
    # Métodos de conveniência
    # ------------------------------------------------------------------

    def get_available_tipologias(self) -> list[TipologiaResult]:
        """Retorna apenas as tipologias com disponibilidade total."""
        return [t for t in self.tipologias if t.is_available]

    def get_tipologia(self, code: str) -> Optional[TipologiaResult]:
        """Retorna a tipologia pelo código (ex: 'T0'), ou None se não existir."""
        return next((t for t in self.tipologias if t.code == code), None)

    def has_any_availability(self) -> bool:
        """True se pelo menos uma tipologia estiver disponível."""
        return len(self.get_available_tipologias()) > 0

    def to_json(self) -> str:
        """Serializa para JSON (para guardar na coluna result_json da BD)."""
        data = {
            "checkin": self.checkin.isoformat(),
            "checkout": self.checkout.isoformat(),
            "nights": self.nights,
            "pax": self.pax,
            "pension": self.pension,
            "tipologias": [t.to_dict() for t in self.tipologias],
            "screenshot_path": self.screenshot_path,
            "query_timestamp": self.query_timestamp,
            "success": self.success,
            "error_message": self.error_message,
        }
        return json.dumps(data, ensure_ascii=False)

    @classmethod
    def from_json(cls, json_str: str) -> "PMSQueryResult":
        """Deserializa a partir de JSON guardado na BD."""
        data = json.loads(json_str)
        return cls(
            checkin=date.fromisoformat(data["checkin"]),
            checkout=date.fromisoformat(data["checkout"]),
            nights=int(data["nights"]),
            pax=int(data["pax"]),
            pension=data["pension"],
            tipologias=[TipologiaResult.from_dict(t) for t in data["tipologias"]],
            screenshot_path=data.get("screenshot_path"),
            query_timestamp=data["query_timestamp"],
            success=bool(data["success"]),
            error_message=data.get("error_message"),
        )

    @classmethod
    def error_result(
        cls,
        checkin: date,
        nights: int,
        pax: int,
        pension: str,
        error_message: str,
    ) -> "PMSQueryResult":
        """
        Factory para criar um resultado de erro sem dados de tipologias.
        Usado quando a pesquisa falha (timeout, login, rede, etc.).
        """
        from datetime import timedelta
        return cls(
            checkin=checkin,
            checkout=checkin + timedelta(days=nights),
            nights=nights,
            pax=pax,
            pension=pension,
            tipologias=[],
            screenshot_path=None,
            query_timestamp=datetime.now().isoformat(),
            success=False,
            error_message=error_message,
        )


# ---------------------------------------------------------------------------
# Modelos de negócio (preparados para os módulos futuros)
# ---------------------------------------------------------------------------

@dataclass
class Client:
    """Representa um cliente identificado pelo número WhatsApp."""
    id: Optional[int]
    phone: str               # Número WhatsApp (ex: "351912345678")
    name: Optional[str]
    email: Optional[str]
    first_seen: Optional[datetime] = None
    last_activity: Optional[datetime] = None


@dataclass
class BookingRequest:
    """Pedido de reserva recolhido pelo bot (nunca confirmado automaticamente)."""
    id: Optional[int]
    conversation_id: Optional[int]
    client_name: Optional[str]
    client_email: Optional[str]
    client_phone: Optional[str]
    checkin: Optional[date]
    checkout: Optional[date]
    nights: Optional[int]
    pax: Optional[int]
    tipologia: Optional[str]
    pension: Optional[str]
    price_per_night_json: Optional[str]   # JSON com preços por noite
    total_price: Optional[float]
    status: str = "pending"               # pending | confirmed | cancelled
    notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
