"""Normalização e comparação de números de telefone.

Módulo isolado para poder ser usado tanto pelo motor como pela base de dados,
sem criar importações circulares entre os dois.

Um mesmo número pode chegar escrito de várias formas — "+351 912 345 678",
"00351912345678", "912345678" — e todas têm de identificar o mesmo contacto,
quer para autorizar (modo teste), quer para guardar e ler a conversa.
"""

import re


def normalize_phone(phone: str) -> str:
    """Forma canónica: só dígitos, sem '+', sem separadores e sem zeros à esquerda."""
    digits = re.sub(r"\D", "", phone or "")
    return digits.lstrip("0")


def same_number(a: str, b: str) -> bool:
    """True se `a` e `b` forem o mesmo telefone escrito em formatos diferentes.

    Aceita a diferença entre número nacional e número com indicativo do país
    ("912345678" vs "351912345678"), mas só quando o mais curto tem 9 dígitos —
    o comprimento de um número nacional português. Evita que dois números
    diferentes passem por iguais por partilharem o mesmo fim.
    """
    da, db = normalize_phone(a), normalize_phone(b)
    if not da or not db:
        return False
    if da == db:
        return True
    short, long = sorted((da, db), key=len)
    return len(short) == 9 and long.endswith(short)
