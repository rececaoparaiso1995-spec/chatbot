"""
rececionista/engine.py — Orquestrador determinístico da conversa.

Fluxo por mensagem:
    gate de modo/horário/números → interpretação (regras) → consulta ao PMS
    FICTÍCIO → composição por template → resposta(s)

Garantias (alinhadas com a especificação):
  * Não responde em DESLIGADO/EMERGÊNCIA.
  * Em modo TESTE só responde a números autorizados.
  * Nunca inventa preços: se não houver oferta, responde com indisponibilidade.
  * Nunca confirma reservas: o pedido fica sempre pendente.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import nlu, settings, templates
from .fake_pms import VILLAS, FictitiousPMS
from .models import BookingDraft, BookingRecord, Offer, Outbound, Session
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
        pms: FictitiousPMS | None = None,
        store: Store | None = None,
    ):
        self.cfg = config or settings.AppConfig.load()
        self.pms = pms or FictitiousPMS()
        self.store = store
        self.sessions: dict[str, Session] = {}

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
    # Disponibilidade e preços
    # ------------------------------------------------------------------

    def _handle_quote(self, session: Session, parsed: nlu.Parsed, lang: str) -> list[Outbound]:
        assert parsed.checkin is not None
        phone = session.phone
        nights = parsed.nights or 0
        if nights <= 0 and parsed.checkout:
            nights = max(0, (parsed.checkout - parsed.checkin).days)
        if nights <= 0:
            return [self._text(phone, templates.t(lang, "ask_nights"))]

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

        offers = self.pms.quote(parsed.checkin, nights, adults, children, meal, villa)

        if not offers:
            message = templates.t(
                lang,
                "no_availability",
                checkin=templates.dmy(parsed.checkin),
                checkout=templates.dmy(parsed.checkin + timedelta(days=nights)),
                guests=adults + children,
            )
            alternatives = self.pms.alternatives(
                parsed.checkin, nights, adults, children, meal, window=7, max_results=3,
                villa_code=villa,
            )
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
            self.sessions[phone] = Session(phone=phone)
        return self.sessions[phone]

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
        self.sessions.clear()
