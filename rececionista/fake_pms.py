"""
rececionista/fake_pms.py — PMS FICTÍCIO (sem qualquer ligação ao Newhotel).

Objetivo: permitir testar o fluxo conversacional completo com preços e
disponibilidade realistas, mas 100% inventados e determinísticos — o mesmo
dia e a mesma villa produzem sempre o mesmo preço, para que os testes
possam ser comparáveis entre execuções.

Regras simuladas:
  - Preço varia de noite para noite (fim de semana, época alta, festivos).
  - Pequeno-almoço acresce por adulto/noite.
  - Algumas noites estão "esgotadas" (determinístico), para exercitar as
    alternativas de datas.
  - Capacidade por tipologia (T0/T1/T2) igual à do config.py do projeto.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

from .models import NightlyRate, Offer, VillaType

BREAKFAST_PER_ADULT = 14.0

VILLAS: tuple[VillaType, ...] = (
    VillaType("T0", "Villa Jardim", max_adults=2, max_children=0, base_rate=165.0),
    VillaType("T1", "Villa Piscina", max_adults=3, max_children=1, base_rate=215.0),
    VillaType("T2", "Villa Panorâmica", max_adults=6, max_children=2, base_rate=295.0),
)

# Períodos deliberadamente esgotados (para testar sugestão de alternativas).
BLACKOUTS: tuple[tuple[str, date, date], ...] = (
    ("T0", date(2026, 12, 28), date(2027, 1, 2)),
    ("T1", date(2027, 2, 12), date(2027, 2, 15)),
)


def _hash01(*parts: object) -> float:
    """Devolve um pseudo-aleatório estável em [0, 1)."""
    raw = "|".join(str(p) for p in parts).encode("utf-8")
    digest = hashlib.sha256(raw).digest()
    return int.from_bytes(digest[:4], "big") / 0xFFFFFFFF


def _is_blackout(code: str, day: date) -> bool:
    for villa_code, start, end in BLACKOUTS:
        if villa_code == code and start <= day <= end:
            return True
    return False


class FictitiousPMS:
    """Substituto do Newhotel para efeitos de teste."""

    #: Probabilidade (0-1) de uma noite estar esgotada.
    sold_out_ratio = 0.20

    # ------------------------------------------------------------------
    # Preços
    # ------------------------------------------------------------------

    def nightly_price(self, villa: VillaType, day: date, meal: str, adults: int) -> float:
        factor = 1.0

        # Época alta (Julho/Agosto) e época média (Junho/Setembro)
        if day.month in (7, 8):
            factor *= 1.45
        elif day.month in (6, 9):
            factor *= 1.20

        # Sexta e sábado
        if day.weekday() in (4, 5):
            factor *= 1.22

        # Festivos de fim de ano
        if (day.month == 12 and day.day >= 20) or (day.month == 1 and day.day <= 2):
            factor *= 1.55

        # Variação estável por villa/dia (±10%)
        factor *= 0.90 + _hash01("rate", villa.code, day.isoformat()) * 0.20

        price = villa.base_rate * factor
        if meal == "BB":
            price += BREAKFAST_PER_ADULT * adults

        return round(price, 2)

    # ------------------------------------------------------------------
    # Disponibilidade
    # ------------------------------------------------------------------

    def is_available(self, villa: VillaType, day: date) -> bool:
        if _is_blackout(villa.code, day):
            return False
        return _hash01("avail", villa.code, day.isoformat()) >= self.sold_out_ratio

    def is_range_available(self, villa: VillaType, checkin: date, nights: int) -> bool:
        return all(
            self.is_available(villa, checkin + timedelta(days=i)) for i in range(nights)
        )

    # ------------------------------------------------------------------
    # Consulta
    # ------------------------------------------------------------------

    def villas_for(self, adults: int, children: int) -> list[VillaType]:
        """Tipologias com capacidade para o número de hóspedes."""
        return [v for v in VILLAS if v.fits(adults, children)]

    def quote(
        self,
        checkin: date,
        nights: int,
        adults: int,
        children: int,
        meal: str = "BB",
        villa_code: str | None = None,
    ) -> list[Offer]:
        """
        Devolve as ofertas disponíveis. Lista vazia = sem disponibilidade
        (nunca inventar: quem chama deve responder com a mensagem de
        indisponibilidade, não com preços aproximados).
        """
        if nights < 1 or adults < 1:
            return []

        candidates = self.villas_for(adults, children)
        if villa_code:
            candidates = [v for v in candidates if v.code == villa_code]

        offers: list[Offer] = []
        for villa in candidates:
            if not self.is_range_available(villa, checkin, nights):
                continue
            nightly = [
                NightlyRate(
                    date=checkin + timedelta(days=i),
                    price=self.nightly_price(
                        villa, checkin + timedelta(days=i), meal, adults
                    ),
                )
                for i in range(nights)
            ]
            offers.append(
                Offer(
                    villa=villa,
                    meal=meal,
                    checkin=checkin,
                    nights=nights,
                    adults=adults,
                    children=children,
                    nightly=nightly,
                    total=round(sum(n.price for n in nightly), 2),
                )
            )
        return offers

    def alternatives(
        self,
        checkin: date,
        nights: int,
        adults: int,
        children: int,
        meal: str = "BB",
        window: int = 7,
        max_results: int = 3,
        villa_code: str | None = None,
    ) -> list[Offer]:
        """
        Procura alternativas em ±`window` dias (mais próximas primeiro).
        Mantém sempre o mesmo número de hóspedes e de noites.
        """
        results: list[Offer] = []
        seen_dates: set[date] = set()

        offsets: list[int] = []
        for delta in range(1, window + 1):
            offsets.extend([delta, -delta])

        for delta in offsets:
            if len(results) >= max_results:
                break
            candidate = checkin + timedelta(days=delta)
            if candidate in seen_dates or candidate < date.today():
                continue
            offers = self.quote(
                candidate, nights, adults, children, meal, villa_code
            )
            if offers:
                seen_dates.add(candidate)
                results.append(offers[0])

        return results[:max_results]
