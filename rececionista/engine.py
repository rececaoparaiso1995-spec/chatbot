"""
rececionista/engine.py — Orquestrador determinístico da conversa.

Fluxo por mensagem:
    gate de modo/horário/números → interpretação (regras) → consulta à fonte de
    disponibilidade (CSV local, se o interruptor estiver ligado; caso contrário
    o pedido é registado) → composição por template → resposta(s)

Garantias (alinhadas com a especificação):
  * Não responde em DESLIGADO/EMERGÊNCIA.
  * Em modo TESTE só responde a números autorizados.
  * Nunca inventa preços: se não houver oferta, responde com indisponibilidade;
    se a fonte falhar ou não cobrir as datas, passa o pedido à receção.
  * Sem navegador: a única fonte além do PMS injetado é o ficheiro CSV.
  * Nunca confirma reservas: o pedido fica sempre pendente.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import availability, llm, nlu, settings, templates
from .fake_pms import VILLAS
from .models import (
    BookingDraft,
    BookingRecord,
    NightlyRate,
    Offer,
    Outbound,
    Session,
)
from .store import Store

log = logging.getLogger("rececionista.engine")

# Rótulos do resumo de pedido (i18n mínimo, mantido aqui por coesão).
SUMMARY_LABELS = {
    "pt": ("Nome", "Email", "Telemóvel", "Entrada", "Saída", "Noites", "Hóspedes",
           "Alojamento", "Regime", "Preço apresentado"),
    "en": ("Name", "Email", "Phone", "Arrival", "Departure", "Nights", "Guests",
           "Villa", "Board", "Quoted price"),
    "es": ("Nombre", "Email", "Teléfono", "Entrada", "Salida", "Noches", "Huéspedes",
           "Alojamiento", "Régimen", "Precio presentado"),
    "fr": ("Nom", "E-mail", "Téléphone", "Arrivée", "Départ", "Nuits", "Personnes",
           "Logement", "Pension", "Prix présenté"),
}


# Reexportados: o resto do código sempre importou estes helpers daqui.
from .phones import normalize_phone, same_number  # noqa: E402


# Até quando o contexto guardado na base de dados é retomado após reiniciar.
STATE_TTL = timedelta(hours=24)


def _offer_to_dict(offer: Offer) -> dict:
    return {
        "villa": offer.villa.code,
        "meal": offer.meal,
        "checkin": offer.checkin.isoformat(),
        "nights": offer.nights,
        "adults": offer.adults,
        "children": offer.children,
        "nightly": [[n.date.isoformat(), n.price] for n in offer.nightly],
        "total": offer.total,
    }


def _offer_from_dict(data: object) -> Offer | None:
    if not isinstance(data, dict):
        return None
    try:
        code = str(data["villa"]).upper()
        villa = next((v for v in VILLAS if v.code == code), None)
        if villa is None:
            return None
        nightly = [
            NightlyRate(date=date.fromisoformat(str(day)), price=float(price))
            for day, price in data.get("nightly", [])
        ]
        return Offer(
            villa=villa,
            meal=str(data["meal"]),
            checkin=date.fromisoformat(str(data["checkin"])),
            nights=int(data["nights"]),
            adults=int(data["adults"]),
            children=int(data["children"]),
            nightly=nightly,
            total=float(data["total"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _pending_to_dict(pending: dict) -> dict:
    converted = dict(pending)
    if isinstance(converted.get("checkin"), date):
        converted["checkin"] = converted["checkin"].isoformat()
    if isinstance(converted.get("at"), datetime):
        converted["at"] = converted["at"].isoformat(timespec="seconds")
    return converted


def _pending_from_dict(data: object) -> dict | None:
    if not isinstance(data, dict):
        return None
    try:
        checkin = date.fromisoformat(str(data["checkin"]))
    except (KeyError, TypeError, ValueError):
        return None
    pending: dict = {
        "checkin": checkin,
        "adults": data.get("adults"),
        "children": data.get("children"),
        "meal": data.get("meal"),
        "villa": data.get("villa"),
        "language": data.get("language"),
        "at": None,
    }
    try:
        pending["at"] = datetime.fromisoformat(str(data["at"]))
    except (KeyError, TypeError, ValueError):
        pass
    return pending


def in_window(now: datetime, start: str = "22:30", end: str = "07:00") -> bool:
    """True se `now` estiver dentro da janela (suporta atravessar a meia-noite)."""
    try:
        start_t = time.fromisoformat(start)
        end_t = time.fromisoformat(end)
    except ValueError:
        return True
    current = now.time()
    if start_t <= end_t:
        return start_t <= current <= end_t
    return current >= start_t or current <= end_t


class Engine:
    """Motor de conversa independente do canal (simulador ou WhatsApp Web)."""

    def __init__(
        self,
        config: settings.AppConfig | None = None,
        pms: object | None = None,
        store: Store | None = None,
        llm_client: object | None = None,
    ):
        self.cfg = config or settings.AppConfig.load()
        # Fontes injetadas (usadas nos testes); por omissão vêm da configuração.
        self.pms = pms
        self.store = store
        self._llm = llm_client  # type: ignore[assignment]
        self.sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------
    # Fonte de disponibilidade
    # ------------------------------------------------------------------

    def availability_source(self) -> object | None:
        """Devolve a fonte de disponibilidade, ou None se estiver desligada.

        Não existe automação de navegador: ou se lê o ficheiro da receção, ou
        não se consulta nada e o pedido fica para a receção responder.
        """
        if self.pms is not None:
            return self.pms
        if self.cfg.use_availability_file:
            return availability.FilePMS(self.cfg.availability_file)
        return None

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    def gate(self, phone: str, now: datetime) -> tuple[bool, str]:
        mode = self.cfg.mode
        if mode == settings.MODE_EMERGENCIA:
            return False, "emergência ativa"
        if mode == settings.MODE_DESLIGADO:
            return False, "aplicação desligada"
        if self.cfg.respect_schedule and not in_window(
            now, self.cfg.schedule_start, self.cfg.schedule_end
        ):
            return False, "fora do horário automático"
        if mode == settings.MODE_TESTE:
            if not any(same_number(phone, n) for n in self.cfg.test_numbers):
                return False, f"número não autorizado (modo teste): {normalize_phone(phone)}"
        return True, ""

    # ------------------------------------------------------------------
    # Entrada principal
    # ------------------------------------------------------------------

    def handle(self, phone: str, text: str, now: datetime | None = None) -> list[Outbound]:
        now = now or datetime.now()
        phone = normalize_phone(phone)
        allowed, reason = self.gate(phone, now)

        if self.store:
            self.store.save_message(
                phone, "in", text, intent=("" if allowed else reason), created_at=now
            )

        if not allowed:
            log.info("Mensagem de %s ignorada: %s", phone, reason)
            return []

        parsed = nlu.parse(text, today=now.date(), default_language=self.cfg.default_language)
        session = self._session(phone)

        # Contexto primeiro: uma resposta como "2" a "quantas noites?" resolve-se
        # com o que já está na sessão, sem gastar uma chamada ao modelo.
        parsed = self._apply_session_context(session, parsed)
        # As regras resolvem a maioria das mensagens. Só quando não percebem
        # nada é que o modelo local é consultado (custa alguns segundos).
        if self.cfg.use_llm and parsed.intent == "unclear":
            parsed = self._assist_with_llm(session, text, parsed, now)
            # O modelo pode trazer as noites que as regras não leram: completar
            # a consulta pendente com o que ele devolveu.
            parsed = self._apply_session_context(session, parsed)

        lang = parsed.language or session.language or self.cfg.default_language
        session.language = lang
        session.last_activity = now

        out: list[Outbound] = []
        if not session.greeted:
            out.append(self._text(phone, templates.t(lang, "intro")))
            session.greeted = True

        if session.draft is not None:
            out += self._continue_booking(session, parsed, lang)
        elif parsed.intent == "booking_confirm":
            out += self._start_booking(session, parsed, lang)
        elif parsed.has_dates:
            out += self._handle_quote(session, parsed, lang)
        elif session.want_booking and (parsed.name or parsed.email):
            # Cliente deu os dados antes das datas: guardar e pedir as datas.
            session.draft = BookingDraft(
                phone=phone,
                name=parsed.name,
                email=parsed.email,
                client_phone=parsed.phone or phone,
                language=lang,
            )
            out.append(self._text(phone, templates.t(lang, "booking_wait_dates")))
        elif parsed.intent == "photo_request":
            out += self._handle_photos(session, parsed, lang)
        elif parsed.intent == "doc_request":
            out += self._handle_docs(parsed, lang)
        elif parsed.intent == "cancel_change":
            out += self._escalate(phone, lang)
        elif parsed.intent == "greeting":
            out.append(self._text(phone, templates.t(lang, "greeting")))
        elif parsed.intent == "thanks":
            out.append(self._text(phone, templates.t(lang, "thanks")))
        elif parsed.intent == "availability":
            out.append(self._text(phone, templates.t(lang, "ask_dates")))
        else:
            session.clarify_count += 1
            if session.clarify_count >= 2:
                out += self._escalate(phone, lang)
            else:
                out.append(self._text(phone, templates.t(lang, "unclear")))

        for item in out:
            if self.store and item.text:
                self.store.save_message(
                    phone, "out", item.text, language=lang, created_at=now
                )
        self._persist_session(session)
        return out

    def handle_media(self, phone: str, kind: str, now: datetime | None = None) -> list[Outbound]:
        """Mensagens não textuais (áudio, imagem do cliente, documento)."""
        now = now or datetime.now()
        phone = normalize_phone(phone)
        allowed, _ = self.gate(phone, now)
        if not allowed:
            return []
        session = self._session(phone)
        lang = session.language or self.cfg.default_language
        key = "audio" if kind == "audio" else "unclear"
        return [self._text(phone, templates.t(lang, key))]

    # ------------------------------------------------------------------
    # Compreensão assistida (LLM local)
    # ------------------------------------------------------------------

    def llm_client(self) -> llm.OllamaClient | None:
        """Cliente do modelo local, ou None se estiver desligado na configuração."""
        if not self.cfg.use_llm:
            return None
        if self._llm is None:
            self._llm = llm.OllamaClient(
                base_url=self.cfg.ollama_url,
                model=self.cfg.ollama_model,
                timeout=self.cfg.ollama_timeout,
            )
        return self._llm

    def _history(self, session: Session, current: str = "") -> list[tuple[str, str]]:
        """Últimas mensagens da conversa, para o modelo resolver elipses.

        A base devolve as mais recentes primeiro; o modelo recebe-as por ordem
        cronológica e sem a mensagem atual (que é enviada à parte).
        """
        turns: list[tuple[str, str]] = []
        if self.store:
            try:
                rows = list(self.store.conversation(session.phone, limit=7))
            except Exception:  # pragma: no cover - leitura de histórico é opcional
                rows = []
            # `conversation()` já devolve por ordem cronológica.
            if current and rows and rows[-1]["body"] == current:
                rows = rows[:-1]
            for row in rows[-6:]:
                who = "user" if row["direction"] == "in" else "assistant"
                turns.append((who, row["body"]))
        if not turns and session.last_offer is not None:
            offer = session.last_offer
            turns.append(
                (
                    "assistant",
                    f"Orçamento apresentado: {offer.villa.name}, entrada "
                    f"{offer.checkin.isoformat()}, {offer.nights} noite(s), "
                    f"total {offer.total:.2f} EUR.",
                )
            )
        return turns

    def _apply_session_context(self, session: Session, parsed: nlu.Parsed) -> nlu.Parsed:
        """Completa uma resposta curta com o que já se sabe desta conversa.

        Dois casos reais:
          * a app perguntou "quantas noites?" e o cliente respondeu "2" —
            as datas já tinham sido dadas e estavam guardadas;
          * "e se ficássemos só duas noites?" depois de um orçamento —
            aproveita-se a entrada desse orçamento.
        """
        pending = session.pending_quote
        if pending is not None and pending.get("at") is not None:
            age = datetime.now() - pending["at"]
            if age > timedelta(minutes=30):
                session.pending_quote = pending = None

        base = session.last_offer
        if pending is None and base is None:
            return parsed

        checkin = parsed.checkin
        nights = parsed.nights or 0
        adults, children = parsed.adults, parsed.children
        meal, villa = parsed.meal, parsed.villa

        # Resposta a "quantas noites?": "2", "duas noites", "3-4 noites",
        # "fim de semana", "uns dias", "uma semana"…
        # Numa resposta curta manda a interpretação de noites: "3-4 noites"
        # pode ter sido lido como dia/mês pelo parser de datas.
        words = len((parsed.raw or "").split())
        if pending is not None and (checkin is None or words <= 5):
            start = pending["checkin"]
            loose, reason = nlu.extract_nights_loose(parsed.raw or "")

            # "domingo" depois de "quantas noites?" é o dia da saída, contado
            # a partir da entrada guardada (não a partir de hoje).
            if not loose and checkin is not None and isinstance(start, date):
                wk_in, wk_out = nlu.extract_weekdays(parsed.raw or "", date.today())
                if wk_in is not None and wk_out is None:
                    delta = (wk_in.weekday() - start.weekday()) % 7
                    if 1 <= delta <= 14:
                        loose = delta
                        reason = f"saída num {wk_in.strftime('%A')} → {delta} noite(s)"

            if loose:
                log.info("Contexto: %s", reason)
                checkin = start
                nights = loose
                adults = adults or pending.get("adults") or 2
                children = children or pending.get("children") or 0
                meal = meal or pending.get("meal")
                villa = villa or pending.get("villa")
                session.pending_quote = None
            elif checkin is not None:
                return parsed  # deu datas novas e as noites não foram ditas

        if checkin is None:
            if base is None or nights <= 0:
                return parsed
            checkin = base.checkin
            adults, children = base.adults, base.children
            meal, villa = base.meal, base.villa.code

        if nights <= 0:
            return parsed
        if not isinstance(checkin, date) or checkin < date.today():
            return parsed

        log.info(
            "Contexto: %s noite(s) aplicadas à entrada de %s (%s)",
            nights, checkin, "consulta pendente" if pending else "último orçamento",
        )
        return nlu.Parsed(
            raw=parsed.raw,
            language=parsed.language,
            intent=parsed.intent if parsed.intent != "unclear" else "availability",
            checkin=checkin,
            checkout=checkin + timedelta(days=nights),
            nights=nights,
            adults=adults,
            children=children,
            meal=meal,
            villa=villa,
            email=parsed.email,
            phone=parsed.phone,
            name=parsed.name,
            has_dates=True,
        )

    def _assist_with_llm(
        self, session: Session, text: str, parsed: nlu.Parsed, now: datetime
    ) -> nlu.Parsed:
        """Consulta o modelo quando as regras não perceberam a mensagem.

        O resultado é somado ao que as regras já tinham: as regras ganham
        sempre nas entidades que já detetaram, e o modelo só preenche o que
        faltava. Qualquer falha mantém o resultado das regras.
        """
        if parsed.intent != "unclear":
            return parsed
        client = self.llm_client()
        if client is None:
            return parsed

        result = client.interpret(
            text, today=now.date(), history=self._history(session, current=text)
        )
        if result is None or not result.is_useful():
            # Visível no separador Registos: sem isto, uma falha do modelo é
            # indistinguível de "as regras também não perceberam".
            reason = getattr(client, "last_error", "") or (
                "o modelo respondeu, mas sem informação útil"
            )
            if self.store:
                self.store.log_event("WARNING", "llm", f"Modelo não ajudou: {reason}")
            return parsed

        checkin = parsed.checkin or result.checkin
        checkout = parsed.checkout or result.checkout
        nights = parsed.nights or result.nights
        if checkin and checkout and not nights:
            nights = (checkout - checkin).days

        merged = nlu.Parsed(
            raw=parsed.raw,
            language=parsed.language or result.language,
            intent=result.intent,
            checkin=checkin,
            checkout=checkout,
            nights=nights,
            adults=parsed.adults or result.adults,
            children=parsed.children or result.children,
            meal=parsed.meal or result.meal,
            villa=parsed.villa or result.villa,
            email=parsed.email or result.email,
            phone=parsed.phone,
            name=parsed.name or result.name,
            has_dates=bool(checkin),
        )
        log.info(
            "LLM: %r → intent=%s datas=%s/%s noites=%s",
            text[:60], merged.intent, merged.checkin, merged.checkout, merged.nights,
        )
        return merged

    # ------------------------------------------------------------------
    # Disponibilidade e preços
    # ------------------------------------------------------------------

    def _handle_quote(self, session: Session, parsed: nlu.Parsed, lang: str) -> list[Outbound]:
        assert parsed.checkin is not None
        phone = session.phone
        nights = parsed.nights or 0
        if nights <= 0 and parsed.checkout:
            nights = max(0, (parsed.checkout - parsed.checkin).days)
        if nights <= 0:
            # Guardar o que já se sabe: a próxima mensagem pode ser só "2".
            session.pending_quote = {
                "checkin": parsed.checkin,
                "adults": parsed.adults,
                "children": parsed.children,
                "meal": parsed.meal,
                "villa": parsed.villa,
                "language": lang,
                "at": session.last_activity,
            }
            return [self._text(phone, templates.t(lang, "ask_nights"))]

        source = self.availability_source()
        if source is None:
            # Sem fonte de preços: nunca inventar. Regista o pedido.
            return self._register_request(session, parsed, lang, nights)

        adults = parsed.adults or 2
        children = max(0, parsed.children or 0)
        meal = parsed.meal or "BB"  # RF-23: pequeno-almoço por defeito
        villa = parsed.villa

        out: list[Outbound] = []
        if self.cfg.send_acknowledgement:
            out.append(self._text(phone, templates.t(lang, "ack_wait")))

        if adults + children > max(v.max_adults + v.max_children for v in VILLAS):
            out.append(
                self._text(phone, templates.t(lang, "capacity", guests=adults + children))
            )
            session.clarify_count += 1
            return out

        try:
            offers = source.quote(parsed.checkin, nights, adults, children, meal, villa)
        except availability.AvailabilityError as exc:
            log.warning("Disponibilidade indisponível: %s", exc)
            return [
                self._text(phone, templates.t(lang, "cannot_confirm"), escalate=True)
            ]

        if not offers:
            message = templates.t(
                lang,
                "no_availability",
                checkin=templates.dmy(parsed.checkin),
                checkout=templates.dmy(parsed.checkin + timedelta(days=nights)),
                guests=adults + children,
            )
            try:
                alternatives = source.alternatives(
                    parsed.checkin, nights, adults, children, meal, window=7,
                    max_results=3, villa_code=villa,
                )
            except availability.AvailabilityError:
                alternatives = []
            if alternatives:
                message += "\n\n" + templates.t(lang, "alternatives_intro")
                for alt in alternatives:
                    message += "\n" + templates.t(
                        lang,
                        "alt_line",
                        villa=alt.villa.name,
                        checkin=templates.dmy(alt.checkin),
                        checkout=templates.dmy(alt.checkout),
                        nights=alt.nights,
                        total=templates.money(lang, alt.total),
                    )
            else:
                message += "\n\n" + templates.t(lang, "escalate")
                out.append(self._text(phone, message, escalate=True))
                return out
            out.append(self._text(phone, message))
            return out

        session.last_offer = offers[0]
        out.append(self._text(phone, self._compose_offers(offers, lang)))

        if session.want_booking:
            out += self._begin_draft_from_offer(session, session.last_offer, lang)
        return out

    def _register_request(
        self, session: Session, parsed: nlu.Parsed, lang: str, nights: int | None = None
    ) -> list[Outbound]:
        """Sem fonte de preços: regista o pedido e remete a resposta à receção.

        Nunca apresenta valores nesta situação (não há como confirmá-los) e a
        conversa fica marcada para intervenção humana.
        """
        phone = session.phone
        if not parsed.has_dates:
            return [self._text(phone, templates.t(lang, "ask_dates_reception"))]

        total_nights = nights or 0
        if total_nights <= 0:
            session.pending_quote = {
                "checkin": parsed.checkin,
                "adults": parsed.adults,
                "children": parsed.children,
                "meal": parsed.meal,
                "villa": parsed.villa,
                "language": lang,
                "at": session.last_activity,
            }
            return [self._text(phone, templates.t(lang, "ask_nights"))]

        assert parsed.checkin is not None
        guests = (parsed.adults or 2) + max(0, parsed.children or 0)
        message = templates.t(
            lang,
            "request_noted",
            checkin=templates.dmy(parsed.checkin),
            checkout=templates.dmy(parsed.checkin + timedelta(days=total_nights)),
            guests=guests,
        )
        return [self._text(phone, message, escalate=True)]

    def _compose_offers(self, offers: list[Offer], lang: str) -> str:
        blocks: list[str] = [templates.t(lang, "offers_intro")]
        for offer in offers:
            blocks.append("")
            blocks.append(
                templates.t(
                    lang,
                    "offer_header",
                    villa=offer.villa.name,
                    meal=templates.meal_label(lang, offer.meal),
                    nights=offer.nights,
                    guests=offer.guests,
                )
            )
            for night in offer.nightly:
                blocks.append(
                    templates.t(
                        lang,
                        "night_line",
                        date=templates.dmy(night.date),
                        price=templates.money(lang, night.price),
                    )
                )
            blocks.append(
                templates.t(lang, "total_line", total=templates.money(lang, offer.total))
            )
        return "\n".join(blocks)

    # ------------------------------------------------------------------
    # Fotografias e documentos
    # ------------------------------------------------------------------

    def _handle_photos(self, session: Session, parsed: nlu.Parsed, lang: str) -> list[Outbound]:
        phone = session.phone
        villa_code = parsed.villa or (session.last_offer.villa.code if session.last_offer else None)
        folders = (
            [settings.PHOTOS_DIR / villa_code]
            if villa_code
            else [p for p in settings.PHOTOS_DIR.iterdir() if p.is_dir()]
        )
        files: list[Path] = []
        for folder in folders:
            if not folder.is_dir():
                continue
            files.extend(
                sorted(
                    p for p in folder.iterdir()
                    if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
                )
            )
        if not files:
            return [self._text(phone, templates.t(lang, "photos_none"))]

        selected = [str(p) for p in files[: self.cfg.max_photos_per_send]]
        villa_name = next(
            (v.name for v in VILLAS if v.code == villa_code), "Paraíso Villas & Spa"
        )
        return [
            Outbound(
                kind="image",
                text=templates.t(lang, "photos_caption", villa=villa_name),
                images=selected,
                phone=phone,
            )
        ]

    def _handle_docs(self, parsed: nlu.Parsed, lang: str) -> list[Outbound]:
        topic_words = [
            nlu.strip_accents(w) for w in re.findall(r"\w+", parsed.raw.lower()) if len(w) > 3
        ]
        best: tuple[int, str, Path] | None = None
        for path in settings.DOCS_DIR.rglob("*"):
            if path.suffix.lower() not in {".txt", ".md"}:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lowered = nlu.strip_accents(content.lower())
            score = sum(1 for word in topic_words if word in lowered)
            if score and (best is None or score > best[0]):
                best = (score, content, path)

        if best is None:
            return self._escalate("", lang, session_phone=None)

        excerpt = best[1].strip().replace("\n\n", "\n")[:600]
        first_line = next((line.strip() for line in best[1].splitlines() if line.strip()), "")
        topic = first_line if 3 < len(first_line) <= 80 else best[2].stem.replace("_", " ")
        return [
            Outbound(
                text=templates.t(lang, "doc_found", topic=topic, excerpt=excerpt),
                meta={"source": str(best[2])},
            )
        ]

    # ------------------------------------------------------------------
    # Pedido de reserva
    # ------------------------------------------------------------------

    def _start_booking(self, session: Session, parsed: nlu.Parsed, lang: str) -> list[Outbound]:
        phone = session.phone
        if self.availability_source() is None:
            # Sem preços: registar a intenção e remeter para a receção.
            return self._register_request(session, parsed, lang)
        if session.last_offer is None:
            session.want_booking = True
            return [self._text(phone, templates.t(lang, "booking_wait_dates"))]

        out = self._begin_draft_from_offer(session, session.last_offer, lang)
        assert session.draft is not None
        # Aproveitar dados que já venham na mensagem de confirmação
        self._merge_into_draft(session.draft, parsed)
        if session.draft.is_complete():
            return self._finalize_booking(session, lang)
        return out

    def _begin_draft_from_offer(self, session: Session, offer: Offer, lang: str) -> list[Outbound]:
        session.draft = BookingDraft(
            phone=session.phone,
            checkin=offer.checkin,
            checkout=offer.checkout,
            nights=offer.nights,
            adults=offer.adults,
            children=offer.children,
            villa=offer.villa.code,
            meal=offer.meal,
            total=offer.total,
            nightly_json=offer.nightly_json(),
            language=lang,
            client_phone=session.phone,
        )
        session.want_booking = False
        return [self._text(session.phone, templates.t(lang, "booking_ask"))]

    def _continue_booking(self, session: Session, parsed: nlu.Parsed, lang: str) -> list[Outbound]:
        draft = session.draft
        assert draft is not None

        # Ainda sem datas: se chegarem agora, apresentar primeiro o preço
        # (RF-25) e retomar a recolha com os dados já fornecidos.
        if draft.checkin is None and parsed.has_dates:
            saved_name, saved_email = draft.name, draft.email
            session.draft = None
            session.want_booking = True
            outs = self._handle_quote(session, parsed, lang)
            if session.draft is not None:
                session.draft.name = session.draft.name or saved_name
                session.draft.email = session.draft.email or saved_email
                if session.draft.is_complete():
                    outs += self._finalize_booking(session, lang)
            return outs

        self._merge_into_draft(draft, parsed)
        draft.language = lang

        if draft.is_complete():
            return self._finalize_booking(session, lang)

        draft.asked += 1
        missing = draft.missing()
        fields = self._fields_label(missing, lang)

        if draft.asked >= 3:
            self._save_draft(draft, status="incomplete")
            session.draft = None
            message = templates.t(
                lang, "booking_partial", summary=self._summary(draft, lang), fields=fields
            ) + "\n\n" + templates.t(lang, "escalate")
            return [self._text(session.phone, message, escalate=True)]

        return [
            self._text(session.phone, templates.t(lang, "booking_need", fields=fields))
        ]

    def _merge_into_draft(self, draft: BookingDraft, parsed: nlu.Parsed) -> None:
        if parsed.name and not draft.name:
            draft.name = parsed.name
        if parsed.email:
            draft.email = parsed.email
        if parsed.phone:
            draft.client_phone = parsed.phone
        if parsed.adults:
            draft.adults = parsed.adults
            draft.children = max(0, parsed.children)
        if parsed.meal:
            draft.meal = parsed.meal
        if parsed.checkin:
            nights = parsed.nights or (
                (parsed.checkout - parsed.checkin).days if parsed.checkout else draft.nights
            )
            if nights and nights > 0:
                offers = self.pms.quote(
                    parsed.checkin, nights, draft.adults, draft.children, draft.meal,
                    draft.villa,
                )
                draft.checkin = parsed.checkin
                draft.checkout = parsed.checkin + timedelta(days=nights)
                draft.nights = nights
                if offers:
                    draft.total = offers[0].total
                    draft.nightly_json = offers[0].nightly_json()

    def _finalize_booking(self, session: Session, lang: str) -> list[Outbound]:
        draft = session.draft
        assert draft is not None
        record = self._save_draft(draft, status="pending")
        session.draft = None
        session.clarify_count = 0
        message = templates.t(
            lang, "booking_saved", summary=self._summary(draft, lang)
        )
        return [
            self._text(session.phone, message, booking_id=record, meta_reason="pedido")
        ]

    def _save_draft(self, draft: BookingDraft, status: str) -> int:
        record = BookingRecord(
            phone=draft.phone,
            name=draft.name or "",
            email=draft.email or "",
            client_phone=draft.client_phone or draft.phone,
            checkin=draft.checkin.isoformat() if draft.checkin else "",
            checkout=draft.checkout.isoformat() if draft.checkout else "",
            nights=draft.nights or 0,
            adults=draft.adults,
            children=draft.children,
            villa=draft.villa or "",
            meal=draft.meal,
            total=draft.total or 0.0,
            nightly_json=draft.nightly_json or "",
            status=status,
        )
        if self.store is None:
            return 0
        booking_id = self.store.save_booking(record)
        self.store.log_event(
            "INFO", "engine", f"Pedido de reserva #{booking_id} guardado ({status})"
        )
        return booking_id

    def _summary(self, draft: BookingDraft, lang: str) -> str:
        labels = SUMMARY_LABELS.get(lang, SUMMARY_LABELS["pt"])
        villa_name = next(
            (f"{v.name} ({v.code})" for v in VILLAS if v.code == draft.villa),
            draft.villa or "-",
        )
        guests = f"{draft.adults}"
        if draft.children:
            guests += f" + {draft.children}"
        lines = [
            f"• {labels[0]}: {draft.name or '-'}",
            f"• {labels[1]}: {draft.email or '-'}",
            f"• {labels[2]}: {draft.client_phone or draft.phone}",
            f"• {labels[3]}: {templates.dmy(draft.checkin) if draft.checkin else '-'}",
            f"• {labels[4]}: {templates.dmy(draft.checkout) if draft.checkout else '-'}",
            f"• {labels[5]}: {draft.nights or '-'}",
            f"• {labels[6]}: {guests}",
            f"• {labels[7]}: {villa_name}",
            f"• {labels[8]}: {templates.meal_label(lang, draft.meal)}",
            f"• {labels[9]}: "
            + (templates.money(lang, draft.total) if draft.total else "-"),
        ]
        return "\n".join(lines)

    def _fields_label(self, missing: list[str], lang: str) -> str:
        mapping = {
            "name": templates.t(lang, "field_name"),
            "email": templates.t(lang, "field_email"),
            "dates": templates.t(lang, "field_dates"),
            "phone": templates.t(lang, "field_phone"),
        }
        return ", ".join(mapping[m] for m in missing if m in mapping) or "-"

    # ------------------------------------------------------------------
    # Fecho de turno
    # ------------------------------------------------------------------

    def end_shift(self, active_minutes: int = 60, now: datetime | None = None) -> list[Outbound]:
        now = now or datetime.now()
        cutoff = now - timedelta(minutes=active_minutes)
        out: list[Outbound] = []
        for session in self.sessions.values():
            if session.last_activity and session.last_activity >= cutoff:
                lang = session.language or self.cfg.default_language
                out.append(
                    self._text(session.phone, templates.t(lang, "closing"), closing=True)
                )
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _session(self, phone: str) -> Session:
        if phone not in self.sessions:
            self.sessions[phone] = self._restore_session(phone) or Session(phone=phone)
        return self.sessions[phone]

    # ------------------------------------------------------------------
    # Contexto guardado (sobrevive a reiniciar a app)
    # ------------------------------------------------------------------

    def _restore_session(self, phone: str) -> Session | None:
        """Recupera o contexto deste número guardado na base de dados."""
        if not self.store:
            return None
        try:
            loaded = self.store.load_session_state(phone)
        except Exception as exc:  # pragma: no cover - leitura opcional
            log.warning("Não foi possível ler o contexto guardado: %s", exc)
            return None
        if loaded is None:
            return None

        payload, stamp = loaded
        if datetime.now() - stamp > STATE_TTL:
            try:
                self.store.clear_session_state(phone)
            except Exception:  # pragma: no cover
                pass
            return None

        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None

        session = Session(phone=phone)
        language = data.get("language")
        session.language = language if isinstance(language, str) and language else None
        session.greeted = bool(data.get("greeted"))
        session.last_offer = _offer_from_dict(data.get("last_offer"))
        session.pending_quote = _pending_from_dict(data.get("pending_quote"))
        session.last_activity = stamp
        if session.last_offer is not None or session.pending_quote is not None:
            log.info("Contexto de %s retomado da base de dados", phone)
        return session

    def _persist_session(self, session: Session) -> None:
        """Guarda o contexto da conversa para o próximo arranque."""
        if not self.store:
            return
        payload = json.dumps(
            {
                "last_offer": (
                    _offer_to_dict(session.last_offer) if session.last_offer else None
                ),
                "pending_quote": (
                    _pending_to_dict(session.pending_quote)
                    if session.pending_quote
                    else None
                ),
                "language": session.language,
                "greeted": session.greeted,
            },
            ensure_ascii=False,
        )
        try:
            self.store.save_session_state(session.phone, payload)
        except Exception as exc:  # pragma: no cover - não pode quebrar a resposta
            log.warning("Não foi possível guardar o contexto: %s", exc)

    def _text(
        self,
        phone: str,
        text: str,
        escalate: bool = False,
        closing: bool = False,
        booking_id: int = 0,
        meta_reason: str = "",
    ) -> Outbound:
        meta: dict = {}
        if escalate:
            meta["escalate"] = True
        if closing:
            meta["closing"] = True
        if booking_id:
            meta["booking_id"] = booking_id
        return Outbound(kind="text", text=text, phone=phone, meta=meta)

    def _escalate(self, phone: str, lang: str, session_phone: str | None = None) -> list[Outbound]:
        target = session_phone or phone
        return [
            self._text(target, templates.t(lang, "escalate"), escalate=True)
        ]

    def reset_sessions(self) -> None:
        for phone in list(self.sessions):
            if self.store:
                try:
                    self.store.clear_session_state(phone)
                except Exception:  # pragma: no cover
                    pass
        self.sessions.clear()
