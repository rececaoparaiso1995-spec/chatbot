"""
rececionista/llm.py — Interpretação de mensagens com um modelo local (Ollama).

Este módulo NÃO escreve respostas ao cliente nem inventa factos: pede ao
modelo um JSON com a intenção e as entidades que as regras não apanharam.
Preços, disponibilidade e confirmações continuam a vir do motor e dos
templates — o modelo só ajuda a *compreender* a mensagem.

Sem dependências: fala com o Ollama por HTTP com a biblioteca padrão.

Se o Ollama não estiver a correr, demorar demasiado ou devolver algo que não
seja JSON válido, `interpret()` devolve None e o motor segue pelas regras,
como fazia antes. Uma falha do modelo nunca bloqueia uma resposta.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime

log = logging.getLogger("rececionista.llm")

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3:4b"

ALLOWED_INTENTS = (
    "booking_confirm",
    "availability",
    "photo_request",
    "doc_request",
    "cancel_change",
    "greeting",
    "thanks",
    "unclear",
)

ALLOWED_VILLAS = ("T0", "T1", "T2")
ALLOWED_MEALS = ("BB", "RO")
ALLOWED_LANGUAGES = ("pt", "en", "es", "fr")


class LlmError(Exception):
    """O Ollama não respondeu de forma utilizável."""


@dataclass
class LlmResult:
    """O que o modelo entendeu da mensagem."""

    intent: str = "unclear"
    language: str | None = None
    checkin: date | None = None
    checkout: date | None = None
    nights: int | None = None
    adults: int | None = None
    children: int = 0
    meal: str | None = None
    villa: str | None = None
    name: str | None = None
    email: str | None = None
    raw: str = ""

    def is_useful(self) -> bool:
        return self.intent != "unclear" or self.checkin is not None or self.nights is not None


_SYSTEM = """És o componente de compreensão de um rececionista virtual de um hotel \
(Paraíso Villas & Spa, Portugal). Lês mensagens de hóspedes em português, inglês, \
espanhol ou francês e devolves a intenção e os dados úteis.

Responde APENAS com um objeto JSON, sem explicações, sem markdown, com exatamente \
estas chaves:

{{"intent": "...", "language": "pt|en|es|fr", "checkin": "YYYY-MM-DD|null", \
"checkout": "YYYY-MM-DD|null", "nights": number|null, "adults": number|null, \
"children": number, "meal": "BB|RO|null", "villa": "T0|T1|T2|null", \
"name": "string|null", "email": "string|null"}}

Regras:
- intent tem de ser um destes: {intents}.
  availability = pergunta por disponibilidade, preços, noites ou estadia.
  booking_confirm = quer avançar com a reserva.
  photo_request = pede fotografias. doc_request = pede informações de spa, \
restaurante, política, wifi, piscina, estacionamento.
  cancel_change = quer alterar ou cancelar uma reserva existente.
  greeting = apenas cumprimento. thanks = apenas agradecimento.
  unclear = não se percebe.
- meal: BB = com pequeno-almoço, RO = sem pequeno-almoço/só alojamento.
- villa: T0, T1 ou T2 quando o cliente se refere a uma tipologia.
- Nunca inventes datas: usa null se não forem ditas ou claramente implícitas \
no contexto da conversa. "duas noites" é nights=2 (datas ficam null).
- O dia de hoje é {today}. Interpreta datas relativas ("amanhã", "próximo fim \
de semana", "no Natal") em relação a hoje.
- Datas de saída nunca são antes da entrada.
"""


def _json_object(text: str) -> dict:
    """Extrai o primeiro objeto JSON de uma resposta do modelo."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("```")[1] if "```" in cleaned[3:] else cleaned[3:]
        cleaned = cleaned.lstrip("json").strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        raise LlmError(f"resposta sem JSON: {text[:120]!r}")
    try:
        data = json.loads(cleaned[start : end + 1])
    except json.JSONDecodeError as exc:
        raise LlmError(f"JSON inválido: {exc}") from exc
    if not isinstance(data, dict):
        raise LlmError("JSON não é um objeto")
    return data


def _as_date(value: object) -> date | None:
    if not isinstance(value, str):
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _as_int(value: object, minimum: int = 1, maximum: int = 30) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        return None
    if minimum <= number <= maximum:
        return number
    return None


class OllamaClient:
    """Cliente mínimo para o Ollama local."""

    def __init__(
        self,
        base_url: str = DEFAULT_URL,
        model: str = DEFAULT_MODEL,
        timeout: float = 45.0,
    ):
        self.base_url = (base_url or DEFAULT_URL).rstrip("/")
        self.model = model or DEFAULT_MODEL
        self.timeout = float(timeout)
        #: Último motivo de falha, para aparecer no registo da app.
        self.last_error: str = ""

    # ------------------------------------------------------------------
    # Infraestrutura
    # ------------------------------------------------------------------

    def _post(self, path: str, payload: dict, timeout: float | None = None) -> dict:
        url = f"{self.base_url}{path}"
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LlmError(f"Ollama inacessível em {self.base_url}: {exc}") from exc
        except (TimeoutError, OSError) as exc:
            raise LlmError(f"Ollama não respondeu a tempo: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LlmError(f"resposta inválida do Ollama: {exc}") from exc

    def _get(self, path: str, timeout: float = 5.0) -> dict:
        url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise LlmError(f"Ollama inacessível em {self.base_url}: {exc}") from exc
        except (TimeoutError, OSError) as exc:
            raise LlmError(f"Ollama não respondeu a tempo: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise LlmError(f"resposta inválida do Ollama: {exc}") from exc

    def models(self) -> list[str]:
        """Modelos já descarregados (nomes como os do `ollama list`)."""
        data = self._get("/api/tags")
        names = [str(m.get("name", "")) for m in data.get("models", []) if m.get("name")]
        return sorted(names)

    def check(self) -> tuple[bool, str]:
        """Estado pronto a mostrar na interface. Nunca levanta exceções."""
        try:
            names = self.models()
        except LlmError as exc:
            return False, str(exc)
        if not names:
            return False, "Ollama a responder, mas não tem nenhum modelo. Use: ollama pull " + self.model
        if not any(n == self.model or n.startswith(self.model.split(":")[0]) for n in names):
            return False, (
                f"Ollama a responder, mas o modelo '{self.model}' não está descarregado. "
                f"Modelos disponíveis: {', '.join(names)}"
            )
        return True, f"Ollama a responder. Modelo disponível: {self.model}"

    # ------------------------------------------------------------------
    # Interpretação
    # ------------------------------------------------------------------

    def interpret(
        self,
        message: str,
        today: date | None = None,
        history: list[tuple[str, str]] | None = None,
    ) -> LlmResult | None:
        """
        Pede ao modelo a interpretação da mensagem.

        `history` é uma lista de (quem, texto) com as últimas mensagens da
        conversa, para o modelo resolver referências implícitas ("e em
        janeiro?", "só duas noites").

        Devolve None em qualquer falha — quem chama segue pelas regras.
        """
        today = today or date.today()
        system = _SYSTEM.format(today=today.isoformat(), intents=", ".join(ALLOWED_INTENTS))

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        for who, text in (history or [])[-6:]:
            role = "assistant" if who in ("assistant", "bot") else "user"
            messages.append({"role": role, "content": text})
        messages.append({"role": "user", "content": message})

        payload: dict = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 220},
        }
        # Qwen3 "pensa" antes de responder: nessa tarefa (devolver JSON de
        # intenção) isso só acrescenta 10-30 s de espera e estraga o formato.
        if self.model.lower().startswith("qwen3"):
            payload["think"] = False

        self.last_error = ""
        try:
            data = self._post("/api/chat", payload)
        except LlmError as exc:
            log.warning("LLM indisponível: %s", exc)
            self.last_error = str(exc)
            return None

        content = str((data.get("message") or {}).get("content") or "")
        if not content:
            log.warning("LLM devolveu resposta vazia")
            self.last_error = "o modelo devolveu uma resposta vazia"
            return None

        try:
            return self._to_result(content, today)
        except LlmError as exc:
            log.warning("LLM devolveu dados inválidos: %s", exc)
            self.last_error = str(exc)
            return None

    def _to_result(self, content: str, today: date) -> LlmResult:
        data = _json_object(content)

        intent = str(data.get("intent") or "unclear").strip().lower()
        if intent not in ALLOWED_INTENTS:
            intent = "unclear"

        language = str(data.get("language") or "").strip().lower()[:2]
        if language not in ALLOWED_LANGUAGES:
            language = None

        checkin = _as_date(data.get("checkin"))
        checkout = _as_date(data.get("checkout"))
        nights = _as_int(data.get("nights"), 1, 60)
        if checkin and checkout and checkout <= checkin:
            checkout = None
        if checkin and checkout and not nights:
            nights = (checkout - checkin).days
        # Datas no passado não são aceites: o modelo pode ter-se enganado.
        if checkin and checkin < today:
            checkin, checkout, nights = None, None, nights

        meal = str(data.get("meal") or "").strip().upper()[:2]
        if meal not in ALLOWED_MEALS:
            meal = None

        villa = str(data.get("villa") or "").strip().upper()[:2]
        if villa not in ALLOWED_VILLAS:
            villa = None

        name = data.get("name")
        name = name.strip() if isinstance(name, str) and 1 < len(name.strip()) <= 80 else None

        email = data.get("email")
        email = email.strip() if isinstance(email, str) and "@" in email else None

        return LlmResult(
            intent=intent,
            language=language,
            checkin=checkin,
            checkout=checkout,
            nights=nights,
            adults=_as_int(data.get("adults"), 1, 20),
            children=_as_int(data.get("children"), 0, 10) or 0,
            meal=meal,
            villa=villa,
            name=name,
            email=email,
            raw=content[:400],
        )
