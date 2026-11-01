"""
rececionista/nlu.py — Interpretação determinística de mensagens (PT/EN/ES/FR).

Nesta fase NÃO se usa IA: a extração de datas, hóspedes, regime e villa é
feita por regras. Isto valida o desenho definido nos requisitos (o LLM só
entra depois, e apenas para intenção/entidades — nunca para preços).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------


def strip_accents(text: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if unicodedata.category(ch) != "Mn"
    )


def _norm(text: str) -> str:
    return strip_accents(text.lower().replace("’", "'"))


# ---------------------------------------------------------------------------
# Idiomas
# ---------------------------------------------------------------------------

_LANG_HINTS: dict[str, tuple[str, ...]] = {
    "pt": (
        "ola", "olá", "bom dia", "boa noite", "obrigado", "obrigada", "queria",
        "gostaria", "disponibilidade", "pequeno-almoco", "pequeno almoço",
        "hospedes", "hóspedes", "noites", "quarto", "preco", "preço", "reserva",
        "para", "com", "sem", "mais", "voce", "você", "estou",
    ),
    "en": (
        "hello", "hi", "good morning", "good evening", "thanks", "thank you",
        "availability", "breakfast", "night", "nights", "price", "booking",
        "would", "like", "please", "for", "with", "without", "room",
        "i", "we", "is", "the",
    ),
    "es": (
        "hola", "buenos dias", "buenas noches", "gracias", "disponibilidad",
        "desayuno", "noches", "precio", "reserva", "quiero", "quisiera",
        "habitacion", "habitación", "para", "con", "sin", "personas", "el", "la",
    ),
    "fr": (
        "bonjour", "bonsoir", "merci", "disponibilite", "disponibilité",
        "petit-dejeuner", "petit-déjeuner", "nuits", "prix", "reservation",
        "réservation", "je", "voudrais", "nous", "avec", "sans", "chambre",
        "pour", "les", "est",
    ),
}


# Palavras que aparecem em vários idiomas e por isso não discriminam.
_AMBIGUOUS = {
    "a", "e", "o", "i", "de", "do", "da", "em", "para", "con", "sin", "com",
    "la", "el", "les", "est", "is", "the", "for", "with", "without", "we",
    "je", "du", "des", "mai", "no", "si", "se",
}


def detect_language(text: str) -> str | None:
    """
    Deteta o idioma dominante por correspondência de palavras inteiras.

    Devolve None quando não há sinal suficiente ou quando há empate — nesse
    caso o motor mantém o idioma da sessão (nunca muda de idioma por uma
    mensagem curta como "Qual é o horário do spa?").
    """
    normalized = _norm(text)
    tokens = set(re.findall(r"[a-z']+", normalized))

    scores: dict[str, int] = {}
    for lang, hints in _LANG_HINTS.items():
        score = 0
        for hint in hints:
            if hint in _AMBIGUOUS or len(hint) < 3:
                continue
            if " " in hint:
                if hint in normalized:
                    score += 3
            elif hint in tokens:
                score += 2
        scores[lang] = score

    best = max(scores, key=lambda key: scores[key])
    if scores[best] == 0:
        return None
    if list(scores.values()).count(scores[best]) > 1:
        return None  # empate → decidir pelo idioma da sessão
    return best




# ---------------------------------------------------------------------------
# Meses
# ---------------------------------------------------------------------------

_MONTHS: dict[str, int] = {}
for _num, _names in enumerate(
    (
        ("janeiro", "january", "enero", "janvier"),
        ("fevereiro", "february", "febrero", "fevrier"),
        ("marco", "march", "marzo", "mars"),
        ("abril", "april", "avril"),
        ("maio", "may", "mayo", "mai"),
        ("junho", "june", "junio", "juin"),
        ("julho", "july", "julio", "juillet"),
        ("agosto", "august", "aout"),
        ("setembro", "september", "septiembre", "septembre"),
        ("outubro", "october", "octubre", "octobre"),
        ("novembro", "november", "noviembre", "novembre"),
        ("dezembro", "december", "diciembre", "decembre"),
    ),
    start=1,
):
    for _name in _names:
        _MONTHS[_norm(_name)] = _num


def _mkdate(day: int, month: int, year: str | None, today: date) -> date | None:
    try:
        if year:
            y = int(year)
            if y < 100:
                y += 2000
        else:
            y = today.year
            candidate = date(y, month, day)
            if candidate < today - timedelta(days=1):
                y += 1
        return date(y, month, day)
    except ValueError:
        return None


_RE_NUMERIC = re.compile(r"\b(\d{1,2})\s*[/.\-]\s*(\d{1,2})(?:\s*[/.\-]\s*(\d{2,4}))?\b")
# O conector ("12 de dezembro") exige espaço a seguir, para não consumir o
# prefixo "de" de nomes de meses como "december" ou "dezembro".
_RE_TEXTUAL = re.compile(
    r"\b(\d{1,2})\s*(?:(?:de|of|del|du|da)\s+|d')?\s*([a-z]{3,10})\b"
)
_RE_RANGE = re.compile(
    r"\b(\d{1,2})\s*(?:a|ao|ate|até|to|until|hasta|au|al)\s*(\d{1,2})\b"
)
_RE_NIGHTS = re.compile(r"\b(\d{1,2})\s*(?:noites?|nights?|noches?|nuits?|nacht)\b")
_RE_RELATIVE = (
    ("depois de amanha", 2),
    ("day after tomorrow", 2),
    ("pasado manana", 2),
    ("apres-demain", 2),
    ("amanha", 1),
    ("tomorrow", 1),
    ("manana", 1),
    ("demain", 1),
    ("hoje", 0),
    ("today", 0),
    ("hoy", 0),
    ("aujourd'hui", 0),
)


def extract_dates(text: str, today: date) -> tuple[date | None, date | None]:
    """Devolve (checkin, checkout) a partir de linguagem natural."""
    t = _norm(text)

    # Datas numéricas (dd/mm[/yyyy])
    numeric: list[date] = []
    for match in _RE_NUMERIC.finditer(t):
        parsed = _mkdate(int(match.group(1)), int(match.group(2)), match.group(3), today)
        if parsed:
            numeric.append(parsed)
    if len(numeric) >= 2:
        return numeric[0], numeric[1]

    # Datas textuais (12 de dezembro / 12 December / 12 de diciembre)
    textual: list[date] = []
    textual_month: int | None = None
    for match in _RE_TEXTUAL.finditer(t):
        month = _MONTHS.get(match.group(2))
        if not month:
            continue
        parsed = _mkdate(int(match.group(1)), month, None, today)
        if parsed:
            textual.append(parsed)
            textual_month = month
    if len(textual) >= 2:
        return textual[0], textual[1]

    if len(textual) == 1 and textual_month:
        # "de 12 a 15 de dezembro" → intervalo no mesmo mês
        range_match = _RE_RANGE.search(t)
        if range_match:
            a = _mkdate(int(range_match.group(1)), textual_month, None, today)
            b = _mkdate(int(range_match.group(2)), textual_month, None, today)
            if a and b and b > a:
                return a, b
        return textual[0], None

    # Relativos (amanhã, hoje, depois de amanhã)
    for keyword, offset in _RE_RELATIVE:
        if keyword in t:
            return today + timedelta(days=offset), None

    if len(numeric) == 1:
        return numeric[0], None
    return None, None


# ---------------------------------------------------------------------------
# Hóspedes, regime e villa
# ---------------------------------------------------------------------------

_RE_ADULTS = re.compile(
    r"\b(\d{1,2})\s*(?:adultos?|adults?|adultes?|personas?|pessoas?|people|persons?|pax|hospedes|huespedes|hotes)\b"
)
_RE_CHILDREN = re.compile(
    r"\b(\d{1,2})\s*(?:criancas?|criancinha?s?|children|child|ninos?|ninas?|enfants?)\b"
)
_RE_TOTAL = re.compile(
    r"\b(?:somos|para|for|we are|seremos|nous sommes)\s*(\d{1,2})\b"
)

_RE_MEAL_RO = (
    "so alojamento", "apenas alojamento", "sem pequeno", "sem refeicoes",
    "room only", "without breakfast", "no breakfast", "solo alojamiento",
    "sin desayuno", "sans petit", "sans dejeuner", "chambre seulement",
)
_RE_MEAL_BB = (
    "pequeno-almoco", "pequeno almoco", "com pequeno", "breakfast", "bed and breakfast",
    "desayuno", "petit-dejeuner", "petit dejeuner", "meia pensao",
)

_VILLA_PATTERNS = (
    ("T0", ("t0", "villa jardim", "jardim", "garden villa")),
    ("T1", ("t1", "villa piscina", "piscina", "pool villa")),
    ("T2", ("t2", "villa panoramica", "panoramica", "panoramic")),
)


def extract_guests(text: str) -> tuple[int | None, int]:
    t = _norm(text)
    adults: int | None = None
    children = 0

    m = _RE_ADULTS.search(t)
    if m:
        adults = int(m.group(1))
    m = _RE_CHILDREN.search(t)
    if m:
        children = int(m.group(1))

    if adults is None:
        m = _RE_TOTAL.search(t)
        if m:
            total = int(m.group(1))
            adults = max(1, total - children)

    if adults is not None and adults < 1:
        adults = 1
    return adults, children


def extract_meal(text: str) -> str | None:
    t = _norm(text)
    if any(k in t for k in _RE_MEAL_RO):
        return "RO"
    if any(k in t for k in _RE_MEAL_BB):
        return "BB"
    return None


def extract_villa(text: str) -> str | None:
    t = _norm(text)
    for code, patterns in _VILLA_PATTERNS:
        if any(p in t for p in patterns):
            return code
    return None


# ---------------------------------------------------------------------------
# Dados pessoais
# ---------------------------------------------------------------------------

_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b")
_RE_PHONE = re.compile(r"(\+?\d[\d\s\-().]{7,}\d)")
_RE_NAME = re.compile(
    r"(?:o meu nome e|meu nome e|chamo-me|chamo me|my name is|i am|i'm|me llamo|"
    r"mi nombre es|je m'appelle|nome|name|nombre|nom)\s*[:\-]?\s*([^\n,;]{2,60})"
)


# Números por extenso (PT/EN/ES/FR) usados em "duas noites", "three nights".
_NIGHT_WORDS: dict[str, int] = {
    "um": 1, "uma": 1, "one": 1, "uno": 1, "una": 1, "un": 1, "une": 1,
    "dois": 2, "duas": 2, "two": 2, "dos": 2, "deux": 2,
    "tres": 3, "three": 3, "trois": 3,
    "quatro": 4, "four": 4, "cuatro": 4, "quatre": 4,
    "cinco": 5, "five": 5, "cinq": 5,
    "seis": 6, "six": 6,
    "sete": 7, "seven": 7, "sept": 7, "siete": 7,
    "oito": 8, "eight": 8, "huit": 8, "ocho": 8,
    "nove": 9, "nine": 9, "neuf": 9, "nueve": 9,
    "dez": 10, "ten": 10, "dix": 10, "diez": 10,
}

_RE_RANGE_NIGHTS = re.compile(
    r"\b(\d{1,2})\s*(?:-|–|a|ao|ou|e|to|et|y)\s*(\d{1,2})\b(?=\s*(?:noite|dia|night|nuit|noche))"
)
_RE_NIGHT_WORD = re.compile(
    r"\b(" + "|".join(_NIGHT_WORDS) + r")\s+(?:noite|noites|dia|dias|night|nights|nuit|nuits|noche|noches)\b"
)


def extract_nights_loose(text: str) -> tuple[int | None, str]:
    """
    Lê o número de noites de respostas vagas, de forma conservadora.

    Só deve ser usado quando a app perguntou as noites (é aí que "2", "uns
dias" ou "fim de semana" significam noites). Devolve (noites, motivo).

    Casos tratados: `3-4 noites` (usa o limite inferior), `duas noites`,
    `fim de semana` (2), `uma semana` (7), `duas semanas` (14), `uns dias` (2)
    e um número solto (1..30).
    """
    t = _norm(text).strip()
    if not t:
        return None, "vazio"

    # O fim de semana tem de ser testado ANTES de "semana", que está
    # contido em "fim de semana" (senão dava 7 noites).
    if re.search(r"\bfim[- ]?de[- ]?semana\b|\bweekend\b|\bfin de semana\b", t):
        return 2, "fim de semana → 2 noites"

    weeks = {
        "uma semana": 7, "1 semana": 7, "a week": 7, "one week": 7,
        "une semaine": 7, "una semana": 7, "semana": 7, "week": 7,
        "duas semanas": 14, "2 semanas": 14, "two weeks": 14, "quinzena": 14,
    }
    for phrase, nights in weeks.items():
        if phrase in t:
            return nights, f"semana → {nights} noites"

    m = _RE_RANGE_NIGHTS.search(t)
    if m:
        low, high = int(m.group(1)), int(m.group(2))
        if 1 <= low <= 30 and low <= high <= 30:
            return low, f"intervalo {low}-{high} → {low} noites (limite inferior)"

    m = _RE_NIGHT_WORD.search(t)
    if m:
        nights = _NIGHT_WORDS[m.group(1)]
        return nights, f"{m.group(1)} → {nights} noites"

    if re.search(
        r"\b(uns|alguns|algumas|poucos|a few|some|couple of|unos|quelques)\b", t
    ) and re.search(r"\b(dias|noites|days|nights|dias|nuits|noches)\b", t):
        return 2, "\"uns dias\" → 2 noites (estimativa assumida)"

    if len(t.split()) <= 4:
        bare = re.fullmatch(r"(?:cerca de |mais ou menos |apenas |só |so |just )?(\d{1,2})"
                            r"(?:\s*(?:noite|noites|dia|dias|night|nights|nuit|nuits|noche|noches))?", t)
        if bare:
            nights = int(bare.group(1))
            if 1 <= nights <= 30:
                return nights, f"número solto → {nights} noites"

    return None, "sem noites reconhecíveis"


def extract_email(text: str) -> str | None:
    m = _RE_EMAIL.search(text)
    return m.group(0).strip() if m else None


def extract_phone(text: str) -> str | None:
    for m in _RE_PHONE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) >= 9:
            return m.group(0).strip()
    return None


def extract_name(text: str) -> str | None:
    m = _RE_NAME.search(_norm(text))
    if not m:
        return None

    candidate = m.group(1).strip(" .!?")
    # Cortar antes de outro campo ("... e o meu email é ...") ou de um email
    candidate = re.split(
        r"\s+(?:e\s+)?(?:o\s+)?(?:meu\s+)?(?:email|mail|correio|telefone|telemovel|contacto)\b|@|,|;|\|",
        candidate,
    )[0]
    # Limitar a 4 palavras (nomes próprios), descartando dígitos
    words = [w for w in candidate.split() if not any(ch.isdigit() for ch in w)][:4]
    if not words:
        return None

    name = " ".join(words).strip(" .!?")
    if len(name) < 2:
        return None
    if name.lower() in {"aqui", "o", "a", "sr", "sra", "sim", "nao", "quero reservar"}:
        return None
    return name.title()


# ---------------------------------------------------------------------------
# Intenções
# ---------------------------------------------------------------------------

_INTENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("booking_confirm", (
        "quero reservar", "queria reservar", "pretendo reservar", "quero avancar",
        "vamos avancar", "quero fazer a reserva", "confirmo a reserva", "reserve",
        "i want to book", "i'd like to book", "lets book", "book it",
        "quiero reservar", "quiero hacer la reserva", "je voudrais reserver",
        "je veux reserver", "on veut reserver",
    )),
    ("cancel_change", (
        "cancelar", "alterar a reserva", "mudar a reserva", "change my booking",
        "cancel my", "anular", "modificar la reserva", "annuler",
    )),
    ("photo_request", (
        "foto", "fotografia", "imagem", "photo", "picture", "imagenes", "imagen",
        "photos", "bild",
    )),
    ("doc_request", (
        "spa", "massagem", "massage", "restaurante", "restaurant", "carta",
        "menu", "pequeno-almoco", "politica", "cancelamento", "cancellation",
        "horario", "wifi", "estacionamento", "parking", "piscina", "pool",
        "transfer", "praia", "beach", "ginasio", "gym",
    )),
    ("availability", (
        "disponibilidade", "disponivel", "availability", "available", "preco",
        "precos", "price", "prices", "tarifa", "quanto custa", "how much",
        "precio", "prix", "orcamento", "quote", "noites", "nights", "noches",
        "nuits", "villa", "reservar",
    )),
    ("greeting", (
        "ola", "bom dia", "boa tarde", "boa noite", "hello", "hi ", "good morning",
        "good evening", "hola", "buenos dias", "buenas noches", "bonjour", "bonsoir",
    )),
    ("thanks", ("obrigad", "thanks", "thank you", "gracias", "merci", "agradeco")),
)


@dataclass
class Parsed:
    raw: str
    language: str | None
    intent: str
    checkin: date | None
    checkout: date | None
    nights: int | None
    adults: int | None
    children: int
    meal: str | None
    villa: str | None
    email: str | None
    phone: str | None
    name: str | None
    has_dates: bool


def parse(text: str, today: date, default_language: str = "pt") -> Parsed:
    # `language` pode ser None: o motor usa então o idioma da sessão.
    language = detect_language(text)
    t = _norm(text)

    checkin, checkout = extract_dates(text, today)
    nights: int | None = None
    m = _RE_NIGHTS.search(t)
    if m:
        nights = int(m.group(1))
    elif checkin and checkout:
        nights = (checkout - checkin).days
    if checkin and checkout and nights and nights > 0 and not _RE_NIGHTS.search(t):
        # consistência: checkout manda
        nights = (checkout - checkin).days

    adults, children = extract_guests(text)

    intent = "unclear"
    for name, keywords in _INTENT_RULES:
        if any(k in t for k in keywords):
            intent = name
            break
    # Datas explícitas sobem a intenção para disponibilidade
    if checkin and intent in {"unclear", "greeting", "thanks"}:
        intent = "availability"

    return Parsed(
        raw=text,
        language=language,
        intent=intent,
        checkin=checkin,
        checkout=checkout,
        nights=nights,
        adults=adults,
        children=children,
        meal=extract_meal(text),
        villa=extract_villa(text),
        email=extract_email(text),
        phone=extract_phone(text),
        name=extract_name(text),
        has_dates=checkin is not None,
    )
