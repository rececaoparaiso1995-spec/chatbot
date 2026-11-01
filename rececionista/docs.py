"""
rececionista/docs.py — Documentos informativos do hotel (horários, menus, preços).

A app responde a perguntas sobre spa, restaurante, políticas e serviços gerais
lendo ficheiros de texto em `data/teste_app/docs/`. Não há base de dados nem
API: se o ficheiro existir, a resposta sai de lá; se não existir, a app diz
sobre o que tem informação em vez de "passar à receção".

Os ficheiros incluídos são **de teste** — substitua o conteúdo pelos horários e
preços reais do hotel. A app cria os que faltarem, sem tocar nos existentes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .nlu import strip_accents

SAMPLE_DOCS: dict[str, str] = {
    "spa_horarios.txt": (
        "Spa — horários e serviços\n"
        "\n"
        "Horário: todos os dias, das 10h00 às 20h00.\n"
        "Massagens: 50 minutos — 65 €; 80 minutos — 95 €.\n"
        "Massagem de casal: 60 minutos — 140 €.\n"
        "Circuito de águas: acesso livre para hóspedes, das 09h00 às 21h00.\n"
        "Marcações: através da receção ou do telefone interno 200.\n"
        "Cancelamento de tratamentos sem custo até 4 horas antes.\n"
    ),
    "restaurante_carta.txt": (
        "Restaurante — carta resumida\n"
        "\n"
        "Pequeno-almoço: 07h30 às 10h30 (buffet).\n"
        "Almoço: 12h30 às 15h00. Jantar: 19h00 às 22h30.\n"
        "Cozinha mediterrânica com produtos locais.\n"
        "Menu infantil disponível. Opções vegetarianas e sem glúten a pedido.\n"
        "Pratos principais: 18 € a 32 €. Menus: 35 € (almoço) e 45 € (jantar).\n"
        "Reservas de mesa através da receção (telefone interno 100).\n"
    ),
    "politicas_cancelamento.txt": (
        "Política de cancelamento (documento de teste)\n"
        "\n"
        "Reservas com tarifa flexível: cancelamento sem custo até 7 dias antes da chegada.\n"
        "Entre 7 e 48 horas: cobra-se a primeira noite.\n"
        "Menos de 48 horas ou no-show: cobra-se a totalidade da estadia.\n"
        "Reservas com tarifa não reembolsável não permitem alteração nem devolução.\n"
        "Alterações de datas sujeitas a disponibilidade e diferença de tarifa.\n"
    ),
    "servicos_gerais.txt": (
        "Serviços gerais — Paraíso Villas & Spa\n"
        "\n"
        "Check-in: a partir das 15h00. Check-out: até às 12h00.\n"
        "Wi-fi: gratuito em todas as villas e áreas comuns. Rede: ParaisoVillas; "
        "palavra-passe entregue no check-in.\n"
        "Estacionamento: gratuito no interior do resort, com vigilância 24 horas.\n"
        "Piscina exterior: aberta das 08h00 às 20h00, de maio a outubro.\n"
        "Ginásio: aberto 24 horas, com acesso pelo cartão da villa.\n"
        "Transfer aeroporto (Faro): 45 € por trajecto, até 4 pessoas, a pedir na receção.\n"
        "Praia: acesso directo, a 3 minutos a pé; toalhas na receção.\n"
        "Aceitam-se animais de estimação até 15 kg, 25 € por noite.\n"
    ),
}


def ensure_samples(directory: Path) -> list[Path]:
    """Cria os documentos de exemplo que faltarem (nunca substitui os existentes)."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for name, content in SAMPLE_DOCS.items():
        path = directory / name
        if not path.exists():
            try:
                path.write_text(content, encoding="utf-8")
                created.append(path)
            except OSError:
                continue
    return created


# Palavras que aparecem em qualquer pedido e não distinguem documentos.
_GENERIC = {
    "queria", "quero", "gostaria", "gostava", "podia", "podem", "posso",
    "saber", "dizer", "informacao", "informacoes", "informacion", "informaciones",
    "information", "informations", "sobre", "para", "qual", "quais", "quais",
    "tem", "tens", "tenho", "tinha", "que", "quais", "onde", "como", "quando",
    "mais", "todo", "todos", "toda", "todas", "existe", "existem", "ha",
    "obrigado", "obrigada", "favor", "please", "voce", "voces", "hotel",
    "necessito", "preciso", "quanto", "quantos", "quantas", "custa", "custo",
    "disponivel", "disponibilidade", "reserva", "reservar", "estadia", "noite",
    "noites", "dia", "dias",
}


def _terms(text: str) -> list[str]:
    normalised = strip_accents((text or "").lower())
    words: list[str] = []
    for raw in re.findall(r"[a-z0-9]+", normalised):
        if len(raw) < 3 or raw in _GENERIC:
            continue
        # Plural simples: "massagens" → "massagen", "transferes" → "transfer".
        if raw.endswith("es") and len(raw) > 5:
            word = raw[:-2]
        elif raw.endswith("s") and len(raw) > 4:
            word = raw[:-1]
        else:
            word = raw
        if word not in words:
            words.append(word)
    return words


@dataclass
class DocHit:
    path: Path
    title: str
    body: str
    score: int


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return ""


def _split_title(text: str, path: Path) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines()]
    first = next((line for line in lines if line), "")
    first = re.sub(r"\s*\((?:documento de teste|teste)\)\s*$", "", first, flags=re.I)
    if first and len(first) <= 80:
        rest = text.split(first, 1)[-1].strip()
        if rest:
            return first, rest
    return first or path.stem.replace("_", " "), text


def _files(directory: Path) -> list[Path]:
    directory = Path(directory)
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.rglob("*") if p.suffix.lower() in {".txt", ".md"}
    )


def search(directory: Path, query: str) -> DocHit | None:
    """Devolve o documento mais provável para a pergunta, ou None."""
    terms = _terms(query)
    best: DocHit | None = None

    for path in _files(directory):
        text = _read(path)
        if not text:
            continue
        title, body = _split_title(text, path)

        content_terms = set(_terms(text)) | set(_terms(title))
        name_terms = set(_terms(path.stem.replace("_", " ")))
        score = 2 * len([t for t in terms if t in content_terms])
        score += 3 * len([t for t in terms if t in name_terms])
        if score <= 0:
            continue

        hit = DocHit(
            path=path,
            title=title,
            body=body[:900] if body else text[:900],
            score=score,
        )
        if best is None or hit.score > best.score:
            best = hit

    return best


def topics(directory: Path) -> list[str]:
    """Títulos dos documentos disponíveis, para oferecer ao cliente."""
    names: list[str] = []
    for path in _files(directory):
        title, _ = _split_title(_read(path), path)
        if title and title not in names:
            names.append(title)
    return names
