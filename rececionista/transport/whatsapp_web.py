"""
rececionista/transport/whatsapp_web.py — Envio/leitura por WhatsApp Web (Playwright).

AVISO IMPORTANTE
----------------
Este é o componente mais frágil do sistema: não existe API oficial, pelo que
a interação é feita sobre a interface web. Os seletores ficam todos em
`SELECTORS` para poderem ser recalibrados sem tocar na lógica.

Modos de utilização:
  * `conversa_aberta` — escreve na conversa que estiver aberta no Chrome.
    É o modo recomendado para os primeiros testes (mais estável).
  * `por_nome` — pesquisa o contacto e abre a conversa antes de escrever.

Todas as operações do Playwright correm numa única thread (a do worker),
porque a API síncrona não é segura entre threads.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field

try:  # Playwright é opcional: a app funciona em modo simulador sem ele.
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    PLAYWRIGHT_AVAILABLE = True
except Exception:  # pragma: no cover - depende do ambiente
    PLAYWRIGHT_AVAILABLE = False
    PlaywrightTimeoutError = Exception  # type: ignore[assignment,misc]

log = logging.getLogger("rececionista.whatsapp")

WHATSAPP_URL = "https://web.whatsapp.com"

SELECTORS: dict[str, tuple[str, ...]] = {
    "ready": ("#pane-side", 'div[aria-label="Lista de conversas"]'),
    "qr": ('canvas[aria-label*="QR"]', "div[data-ref] canvas"),
    "chat_search": (
        'div[contenteditable="true"][data-tab="3"]',
        'div[title="Pesquisar"]',
    ),
    "search_result": (
        'div[role="listitem"] span[title]',
        "#pane-side div[role=\"listitem\"]",
    ),
    "chat_rows": ('#pane-side div[role="listitem"]',),
    "unread_badge": (
        'span[aria-label*="não lida"]',
        'span[aria-label*="unread"]',
        'span[aria-label*="no leído"]',
        'span[aria-label*="non lu"]',
    ),
    "message_input": (
        'footer div[contenteditable="true"]',
        'div[contenteditable="true"][data-tab="10"]',
    ),
    "incoming": ("div.message-in",),
    "attach": (
        'div[title="Anexar"]',
        'button[title="Anexar"]',
        'span[data-icon="plus"]',
        'span[data-icon="clip"]',
    ),
    "image_input": ('input[accept*="image"]',),
    "caption": ('div[contenteditable="true"][data-tab="undefined"]',),
}


@dataclass
class Event:
    kind: str
    message: str


class WhatsAppWebTransport:
    """Worker dedicado com fila de comandos."""

    def __init__(
        self,
        profile_dir: str,
        poll_seconds: int = 5,
        on_message=None,
        on_event=None,
    ) -> None:
        self.profile_dir = profile_dir
        self.poll_seconds = poll_seconds
        self.on_message = on_message
        self.on_event = on_event or (lambda event: None)

        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = "não iniciado"
        self._ready = False
        self._auto_poll = False
        self._seen: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # API pública (thread-safe, não bloqueia)
    # ------------------------------------------------------------------

    def status(self) -> str:
        return self._status

    @property
    def ready(self) -> bool:
        return self._ready

    def start(self) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            self._set_status("Playwright não instalado — pip install playwright")
            return
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def send_text(self, text: str, chat: str = "") -> None:
        self._queue.put(("send_text", (text, chat)))

    def send_images(self, images: list[str], caption: str = "", chat: str = "") -> None:
        self._queue.put(("send_images", (images, caption, chat)))

    def open_chat(self, chat: str) -> None:
        self._queue.put(("open", (chat,)))

    def set_auto_poll(self, enabled: bool) -> None:
        self._auto_poll = enabled
        self._emit(f"leitura automática {'ligada' if enabled else 'desligada'}")

    def close(self) -> None:
        self._stop.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------

    def _set_status(self, text: str) -> None:
        self._status = text
        self._emit(text)

    def _emit(self, message: str) -> None:
        try:
            self.on_event(Event("status", message))
        except Exception:  # pragma: no cover
            pass

    def _find(self, page, key: str):
        for selector in SELECTORS[key]:
            try:
                element = page.query_selector(selector)
                if element:
                    return element
            except Exception:
                continue
        return None

    def _worker(self) -> None:
        page = None
        context = None
        playwright = None
        try:
            self._set_status("a iniciar Chrome…")
            playwright = sync_playwright().start()
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=self.profile_dir,
                headless=False,
                viewport={"width": 1280, "height": 900},
                args=["--disable-blink-features=AutomationControlled"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(WHATSAPP_URL, wait_until="domcontentloaded", timeout=60_000)
            self._set_status("a carregar WhatsApp Web…")

            start = time.time()
            while time.time() - start < 120:
                if self._find(page, "ready"):
                    self._ready = True
                    self._set_status("WhatsApp Web pronto")
                    break
                if self._find(page, "qr"):
                    self._set_status("leia o QR code com o telemóvel do hotel")
                page.wait_for_timeout(1000)
            if not self._ready:
                self._set_status("WhatsApp Web não ficou pronto (sessão por autenticar)")

            while not self._stop.is_set():
                try:
                    command = self._queue.get(timeout=1)
                except queue.Empty:
                    if self._auto_poll and self._ready:
                        self._poll_once(page)
                    continue
                if command is None:
                    break
                action, args = command
                try:
                    if action == "send_text":
                        self._do_send_text(page, *args)
                    elif action == "send_images":
                        self._do_send_images(page, *args)
                    elif action == "open":
                        self._do_open_chat(page, *args)
                except Exception as exc:  # nunca deixar cair o worker
                    self._set_status(f"erro: {exc}")
                    log.exception("Falha ao executar %s", action)
        except Exception as exc:
            self._set_status(f"falha ao abrir WhatsApp Web: {exc}")
            log.exception("Falha no worker do WhatsApp Web")
        finally:
            self._ready = False
            try:
                if context:
                    context.close()
                if playwright:
                    playwright.stop()
            except Exception:
                pass
            self._set_status("WhatsApp Web fechado")

    # ------------------------------------------------------------------
    # Ações
    # ------------------------------------------------------------------

    def _do_open_chat(self, page, chat: str) -> None:
        if not chat:
            return
        search = self._find(page, "chat_search")
        if not search:
            self._set_status("caixa de pesquisa não encontrada")
            return
        search.click()
        page.keyboard.press("Control+A")
        page.keyboard.type(chat, delay=30)
        page.wait_for_timeout(1200)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1200)

    def _input(self, page):
        return self._find(page, "message_input")

    def _do_send_text(self, page, text: str, chat: str = "") -> None:
        if not text:
            return
        if chat:
            self._do_open_chat(page, chat)
        box = self._input(page)
        if not box:
            self._set_status("campo de mensagem não encontrado")
            return
        box.click()
        for index, line in enumerate(text.split("\n")):
            if index:
                page.keyboard.down("Shift")
                page.keyboard.press("Enter")
                page.keyboard.up("Shift")
            page.keyboard.insert_text(line)
        page.keyboard.press("Enter")
        page.wait_for_timeout(500)
        self._set_status("mensagem enviada")

    def _do_send_images(self, page, images: list[str], caption: str = "", chat: str = "") -> None:
        if not images:
            return
        if chat:
            self._do_open_chat(page, chat)
        attach = self._find(page, "attach")
        if not attach:
            self._set_status("botão de anexar não encontrado")
            return
        with page.expect_file_chooser() as chooser_info:
            attach.click()
        chooser_info.value.set_files(images)
        page.wait_for_timeout(2000)
        if caption:
            field = self._find(page, "caption") or self._input(page)
            if field:
                field.click()
                page.keyboard.insert_text(caption)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        self._set_status(f"{len(images)} imagem(ns) enviada(s)")

    def _poll_once(self, page) -> None:
        rows = []
        for selector in SELECTORS["chat_rows"]:
            try:
                rows = page.query_selector_all(selector)
            except Exception:
                rows = []
            if rows:
                break
        for row in rows[:5]:
            try:
                if not row.query_selector(", ".join(SELECTORS["unread_badge"])):
                    continue
                row.click()
                page.wait_for_timeout(900)
                incoming = page.query_selector_all("div.message-in")
                if not incoming:
                    continue
                chat_name = ""
                header = page.query_selector("header span[title]")
                if header:
                    chat_name = header.get_attribute("title") or ""
                text = incoming[-1].inner_text().strip()
                if not text:
                    continue
                key = (chat_name, text)
                if key in self._seen:
                    continue
                self._seen.add(key)
                if callable(self.on_message):
                    self.on_message(chat_name or "desconhecido", text)
            except Exception:
                continue
