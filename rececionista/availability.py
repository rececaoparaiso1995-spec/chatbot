"""rececionista/availability.py — Disponibilidade lida de um ficheiro local.

Esta é a fonte de preços e disponibilidade. Não há navegador, não há
Newhotel, não há scraping: a aplicação lê um CSV que a receção mantém.

Formato do ficheiro (abre e edita-se no Excel):

    data,villa,disponivel,preco_bb,preco_ro
    2026-12-20,T0,sim,199.00,175.00
    2026-12-20,T1,sim,259.00,229.00
    2026-12-20,T2,nao,,

Regras (alinhadas com os requisitos):

  * Só se apresentam preços que existam no ficheiro. Nunca se calcula,
    estima ou interpola nada.
  * Se o ficheiro não cobrir as datas pedidas, NÃO se afirma
    indisponibilidade: assume-se que não é possível confirmar, e a conversa
    é encaminhada para a receção.
  * O ficheiro é relido quando muda (comparação por data de modificação),
    para que as alterações da receção tenham efeito sem reiniciar a app.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .fake_pms import VILLAS
from .models import NightlyRate, Offer, VillaType

_TRUTHY = {"sim", "s", "yes", "y", "1", "true", "verdadeiro", "disponivel", "disponível"}
_FALSY = {"nao", "não", "n", "no", "0", "false", "falso", "esgotado", "indisponivel", "indisponível"}

COLUMNS_HINT = "data,villa,disponivel,preco_bb,preco_ro"


class AvailabilityError(Exception):
    """Não foi possível obter a disponibilidade (nunca deve virar 'esgotado')."""


class AvailabilityFileError(AvailabilityError):
    """Ficheiro em falta, ilegível ou sem linhas válidas."""


class AvailabilityOutOfRange(AvailabilityError):
    """O ficheiro não cobre as datas pedidas."""


def parse_date(text: str) -> date | None:
    """Aceita `2026-12-20`, `20/12/2026` e `20-12-2026`."""
    raw = (text or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_available(text: str) -> bool | None:
    raw = (text or "").strip().lower()
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return None


def parse_price(text: str) -> float | None:
    """Aceita `259`, `259.50`, `259,50` e `259,50 €`."""
    raw = re.sub(r"[^\d,.\-]", "", (text or "").strip())
    if not raw:
        return None
    if "," in raw and "." in raw:
        # Formato pt: 1.259,50
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        raw = raw.replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


@dataclass(frozen=True)
class _Row:
    available: bool
    price_bb: float | None
    price_ro: float | None

    def price_for(self, meal: str) -> float | None:
        return self.price_bb if meal == "BB" else self.price_ro


class AvailabilityIndex:
    """Conteúdo do ficheiro, indexado por (villa, dia)."""

    def __init__(self, rows: dict[tuple[str, date], _Row], source: Path):
        self.rows = rows
        self.source = source
        self.first = min((day for _, day in rows), default=None)
        self.last = max((day for _, day in rows), default=None)

    @classmethod
    def from_file(cls, path: str | Path) -> "AvailabilityIndex":
        file = Path(path)
        if not file.exists():
            raise AvailabilityFileError(f"ficheiro de disponibilidade não encontrado: {file}")
        try:
            text = file.read_text(encoding="utf-8-sig")
        except OSError as exc:
            raise AvailabilityFileError(f"não foi possível ler {file}: {exc}") from exc

        # Excel em português grava com ';'
        delimiter = ";" if text.count(";") > text.count(",") else ","
        reader = csv.DictReader(text.splitlines(), delimiter=delimiter)

        rows: dict[tuple[str, date], _Row] = {}
        for line, raw in enumerate(reader, start=2):
            villa = (raw.get("villa") or raw.get("tipologia") or "").strip().upper()
            day = parse_date(raw.get("data") or raw.get("date") or "")
            available = parse_available(raw.get("disponivel") or raw.get("available") or "")
            if not villa or day is None or available is None:
                continue  # linha incompleta: ignorada, não inventada
            rows[(villa, day)] = _Row(
                available=available,
                price_bb=parse_price(raw.get("preco_bb") or raw.get("preco_bb_eur") or ""),
                price_ro=parse_price(raw.get("preco_ro") or raw.get("preco_ro_eur") or ""),
            )

        if not rows:
            raise AvailabilityFileError(
                f"{file} não tem linhas válidas (esperado: {COLUMNS_HINT})"
            )
        return cls(rows, file)

    def covers(self, checkin: date, nights: int) -> bool:
        if self.first is None or self.last is None:
            return False
        return self.first <= checkin and checkin + timedelta(days=nights - 1) <= self.last


_CACHE: dict[tuple[str, float], AvailabilityIndex] = {}


def load_index(path: str | Path) -> AvailabilityIndex:
    """Devolve o índice, relendo o ficheiro apenas se este tiver mudado."""
    file = Path(path)
    try:
        stamp = file.stat().st_mtime
    except OSError as exc:
        raise AvailabilityFileError(f"ficheiro inacessível: {file}") from exc
    key = (str(file.resolve()), stamp)
    if key not in _CACHE:
        _CACHE.clear()
        _CACHE[key] = AvailabilityIndex.from_file(file)
    return _CACHE[key]


class FilePMS:
    """Fonte de disponibilidade/preços baseada no ficheiro da receção.

    Cumpre a mesma interface que o PMS fictício (`villas_for`, `quote`,
    `alternatives`), para que o motor não saiba de onde vêm os dados.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    # ------------------------------------------------------------------

    def villas_for(self, adults: int, children: int) -> list[VillaType]:
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
        if nights < 1 or adults < 1:
            return []

        index = load_index(self.path)
        if not index.covers(checkin, nights):
            raise AvailabilityOutOfRange(
                f"o ficheiro cobre {index.first} a {index.last}; "
                f"pedido: {checkin} + {nights} noite(s)"
            )

        candidates = self.villas_for(adults, children)
        if villa_code:
            candidates = [v for v in candidates if v.code == villa_code]

        offers: list[Offer] = []
        for villa in candidates:
            nightly: list[NightlyRate] = []
            for i in range(nights):
                row = index.rows.get((villa.code, checkin + timedelta(days=i)))
                if row is None or not row.available:
                    nightly = []
                    break
                price = row.price_for(meal)
                if price is None:
                    # Sem preço no ficheiro para este regime: não se inventa.
                    nightly = []
                    break
                nightly.append(NightlyRate(date=checkin + timedelta(days=i), price=price))
            if not nightly:
                continue
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
        results: list[Offer] = []
        seen: set[date] = set()

        offsets: list[int] = []
        for delta in range(1, window + 1):
            offsets.extend([delta, -delta])

        for delta in offsets:
            if len(results) >= max_results:
                break
            candidate = checkin + timedelta(days=delta)
            if candidate in seen or candidate < date.today():
                continue
            try:
                offers = self.quote(candidate, nights, adults, children, meal, villa_code)
            except AvailabilityError:
                break  # sem cobertura do ficheiro: parar em vez de arriscar
            if offers:
                seen.add(candidate)
                results.append(offers[0])

        return results[:max_results]


def write_sample(path: str | Path) -> Path:
    """Cria (ou substitui) um ficheiro de exemplo com 45 dias a partir de hoje."""
    file = Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    today = date.today()

    base = {"T0": (199.0, 175.0), "T1": (259.0, 229.0), "T2": (329.0, 295.0)}
    lines = [COLUMNS_HINT]
    for i in range(45):
        day = today + timedelta(days=i)
        weekend = day.weekday() in (4, 5)
        for code, (bb, ro) in base.items():
            factor = 1.20 if weekend else 1.0
            # Um fim de semana esgotado por tipologia, para testar alternativas.
            sold_out = weekend and i % 9 in (0, 1) and code == "T1"
            if sold_out:
                lines.append(f"{day.isoformat()},{code},nao,,")
            else:
                lines.append(
                    f"{day.isoformat()},{code},sim,{bb * factor:.2f},{ro * factor:.2f}"
                )
    file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return file
