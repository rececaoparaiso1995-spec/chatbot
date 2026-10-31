"""
rececionista/gui.py — Interface de teste (Tkinter, sem dependências externas).

Separadores:
  Simulador   — conversa com o motor, sem tocar em WhatsApp.
  Conversas   — histórico guardado (SQLite).
  Pedidos     — pedidos de reserva recolhidos + estados + CSV.
  Configuração— modo, números autorizados, horário, caminhos, WhatsApp.
  Registos    — eventos e erros.

O envio real para WhatsApp Web é opcional e explícito (checkbox / botão).
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from . import settings
from .engine import Engine, normalize_phone
from .store import Store, export_bookings_csv
from .transport.simulator import SimulatorTransport
from .transport.whatsapp_web import PLAYWRIGHT_AVAILABLE, Event, WhatsAppWebTransport

log = logging.getLogger("rececionista.gui")

STATE_COLORS = {
    settings.MODE_AUTOMATICO: "#1f9d55",
    settings.MODE_TESTE: "#b7791f",
    settings.MODE_DESLIGADO: "#4a5568",
    settings.MODE_EMERGENCIA: "#c53030",
}

STATE_LABELS = {
    settings.MODE_AUTOMATICO: "LIGADO · AUTOMÁTICO",
    settings.MODE_TESTE: "LIGADO · MODO TESTE",
    settings.MODE_DESLIGADO: "DESLIGADO",
    settings.MODE_EMERGENCIA: "EMERGÊNCIA — PARAGEM TOTAL",
}

QUICK_PROMPTS = [
    ("Disponibilidade PT", "Boa noite, queria saber disponibilidade de 12/12/2026 a 15/12/2026 para 2 pessoas com pequeno-almoço."),
    ("Disponibilidade EN", "Hello, do you have availability from 12/12/2026 to 15/12/2026 for 2 adults with breakfast?"),
    ("Disponibilidade ES", "Hola, ¿tienen disponibilidad del 12/12/2026 al 15/12/2026 para 2 personas con desayuno?"),
    ("Disponibilidade FR", "Bonjour, avez-vous de la disponibilité du 12/12/2026 au 15/12/2026 pour 2 personnes avec petit-déjeuner ?"),
    ("Sem disponibilidade", "Bom dia, têm algo de 28/12/2026 a 31/12/2026 para 2 pessoas?"),
    ("Fotos", "Podem enviar-me fotografias da villa T2?"),
    ("Spa", "Qual é o horário do spa?"),
    ("Quero reservar", "Quero reservar, o meu nome é Ana Silva e o meu email é ana.silva@example.com"),
    ("Áudio", "[áudio]"),
]


class RecepcionistaApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Rececionista Virtual — Paraíso Villas & Spa · APP DE TESTE")
        self.geometry("1200x780")
        self.minsize(1000, 660)

        self.cfg = settings.AppConfig.load()
        self.store = Store(settings.DB_PATH)
        self.engine = Engine(self.cfg, store=self.store)
        self.simulator = SimulatorTransport()
        self.transport: WhatsAppWebTransport | None = None
        self.inbox: queue.Queue = queue.Queue()
        self.last_outbound: list = []

        self._build()
        self._apply_mode()
        self._load_transcript()
        self.after(300, self._tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Construção da interface
    # ------------------------------------------------------------------

    def _build(self) -> None:
        header = tk.Frame(self, bg="#111827", height=74)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(
            header, text="Paraíso Villas & Spa", bg="#111827", fg="white",
            font=("Segoe UI", 15, "bold"),
        ).pack(side="left", padx=16)

        tk.Label(
            header, text="DADOS FICTÍCIOS · SEM NEWHOTEL", bg="#374151", fg="#fbbf24",
            font=("Segoe UI", 9, "bold"), padx=10, pady=4,
        ).pack(side="left")

        self.state_label = tk.Label(
            header, text="", bg="#4a5568", fg="white",
            font=("Segoe UI", 11, "bold"), padx=16, pady=8,
        )
        self.state_label.pack(side="right", padx=16)

        controls = tk.Frame(self, bg="#f3f4f6")
        controls.pack(fill="x")

        for text, mode, bg in (
            ("Ligar (automático)", settings.MODE_AUTOMATICO, "#1f9d55"),
            ("Modo teste", settings.MODE_TESTE, "#b7791f"),
            ("Desligar", settings.MODE_DESLIGADO, "#4a5568"),
        ):
            tk.Button(
                controls, text=text, bg=bg, fg="white", relief="flat",
                font=("Segoe UI", 9, "bold"), padx=14, pady=6,
                command=lambda m=mode: self._set_mode(m),
            ).pack(side="left", padx=(10, 0), pady=8)

        tk.Button(
            controls, text="⚠ EMERGÊNCIA", bg="#c53030", fg="white", relief="flat",
            font=("Segoe UI", 9, "bold"), padx=18, pady=6,
            command=self._emergency,
        ).pack(side="left", padx=(18, 10), pady=8)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._build_simulator()
        self._build_conversations()
        self._build_bookings()
        self._build_settings()
        self._build_logs()

    # ----------------------------- Simulador ---------------------------

    def _build_simulator(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Simulador")

        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True, padx=(0, 8), pady=8)

        self.chat = tk.Text(
            left, wrap="word", state="disabled", bg="#fbfbfd", relief="flat",
            font=("Segoe UI", 10), padx=10, pady=10,
        )
        self.chat.pack(fill="both", expand=True)
        self.chat.tag_configure("cliente", foreground="#1e3a8a", spacing1=8, spacing3=4)
        self.chat.tag_configure("bot", foreground="#065f46", spacing1=8, spacing3=4)
        self.chat.tag_configure("sistema", foreground="#9a3412", spacing1=8, spacing3=4)

        entry_row = ttk.Frame(left)
        entry_row.pack(fill="x", pady=(8, 0))

        self.entry = ttk.Entry(entry_row, font=("Segoe UI", 10))
        self.entry.pack(side="left", fill="x", expand=True)
        self.entry.bind("<Return>", lambda _event: self._send())
        ttk.Button(entry_row, text="Enviar", command=self._send).pack(side="left", padx=6)
        ttk.Button(entry_row, text="Limpar", command=self._clear_chat).pack(side="left")

        quick = ttk.Frame(left)
        quick.pack(fill="x", pady=(6, 0))
        for label, prompt in QUICK_PROMPTS:
            ttk.Button(
                quick, text=label, width=18,
                command=lambda p=prompt: self._quick(p),
            ).pack(side="left", padx=(0, 4), pady=2)

        right = ttk.LabelFrame(tab, text="Canal WhatsApp Web (opcional)")
        right.pack(side="right", fill="y", padx=(0, 0), pady=8)
        right.configure(width=340)

        ttk.Label(right, text="Número do cliente (simulador)").pack(anchor="w", padx=10, pady=(10, 0))
        self.phone_var = tk.StringVar(value=self.cfg.simulator_phone)
        ttk.Entry(right, textvariable=self.phone_var, width=28).pack(anchor="w", padx=10, pady=4)

        ttk.Separator(right).pack(fill="x", padx=10, pady=10)

        ttk.Label(right, text="Estado do WhatsApp Web", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10)
        self.wa_status = tk.StringVar(value="não iniciado")
        ttk.Label(right, textvariable=self.wa_status, wraplength=300, foreground="#374151").pack(
            anchor="w", padx=10, pady=(2, 6)
        )

        ttk.Button(right, text="Abrir WhatsApp Web (Chrome)", command=self._start_whatsapp).pack(
            fill="x", padx=10, pady=2
        )
        ttk.Button(right, text="Enviar última resposta", command=self._send_last).pack(
            fill="x", padx=10, pady=2
        )

        self.auto_forward = tk.BooleanVar(value=self.cfg.auto_forward)
        ttk.Checkbutton(
            right, text="Enviar automaticamente cada resposta",
            variable=self.auto_forward, command=self._save_runtime,
        ).pack(anchor="w", padx=10, pady=(10, 4))

        ttk.Label(right, text="Modo de envio").pack(anchor="w", padx=10)
        self.send_mode = tk.StringVar(value=self.cfg.whatsapp_send_mode)
        ttk.Combobox(
            right, textvariable=self.send_mode, state="readonly",
            values=("conversa_aberta", "por_nome"), width=26,
        ).pack(anchor="w", padx=10, pady=2)

        ttk.Label(right, text="Contacto de destino (modo por_nome)").pack(anchor="w", padx=10, pady=(8, 0))
        self.target_var = tk.StringVar(value=self.cfg.whatsapp_target)
        ttk.Entry(right, textvariable=self.target_var, width=28).pack(anchor="w", padx=10, pady=4)

        self.auto_poll = tk.BooleanVar(value=self.cfg.whatsapp_auto_poll)
        ttk.Checkbutton(
            right, text="Ler mensagens recebidas (experimental)",
            variable=self.auto_poll, command=self._toggle_poll,
        ).pack(anchor="w", padx=10, pady=(10, 4))

        ttk.Label(
            right,
            text=(
                "Nota: no modo conversa_aberta, abra no Chrome a conversa do número\n"
                "autorizado antes de enviar. O modo por_nome pesquisa o contacto."
            ),
            foreground="#6b7280", wraplength=300, justify="left",
        ).pack(anchor="w", padx=10, pady=(12, 10))

        if not PLAYWRIGHT_AVAILABLE:
            ttk.Label(
                right, text="Playwright não instalado — envio real indisponível.",
                foreground="#b91c1c", wraplength=300,
            ).pack(anchor="w", padx=10, pady=(0, 10))

    # ---------------------------- Conversas ----------------------------

    def _build_conversations(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Conversas")

        top = ttk.Frame(tab)
        top.pack(fill="x", pady=6)
        ttk.Button(top, text="Atualizar", command=self._refresh_conversations).pack(side="left")

        body = ttk.Frame(tab)
        body.pack(fill="both", expand=True)

        self.conv_list = tk.Listbox(body, width=34, font=("Segoe UI", 9))
        self.conv_list.pack(side="left", fill="y")
        self.conv_list.bind("<<ListboxSelect>>", self._on_conversation_select)

        self.conv_text = tk.Text(body, wrap="word", state="disabled", font=("Segoe UI", 10))
        self.conv_text.pack(side="left", fill="both", expand=True, padx=(8, 0))

        self._refresh_conversations()

    # ----------------------------- Pedidos -----------------------------

    def _build_bookings(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Pedidos de reserva")

        actions = ttk.Frame(tab)
        actions.pack(fill="x", pady=6)
        ttk.Button(actions, text="Atualizar", command=self._refresh_bookings).pack(side="left")
        ttk.Button(actions, text="Confirmar pela receção", command=lambda: self._set_booking("confirmed")).pack(side="left", padx=6)
        ttk.Button(actions, text="Recusar/cancelar", command=lambda: self._set_booking("cancelled")).pack(side="left")
        ttk.Button(actions, text="Exportar CSV", command=self._export_bookings).pack(side="left", padx=6)

        columns = ("id", "created_at", "status", "name", "villa", "checkin", "checkout", "total", "phone")
        self.bookings_tree = ttk.Treeview(tab, columns=columns, show="headings", height=16)
        widths = {"id": 50, "created_at": 140, "status": 100, "name": 160, "villa": 70,
                  "checkin": 100, "checkout": 100, "total": 100, "phone": 130}
        for col in columns:
            self.bookings_tree.heading(col, text=col)
            self.bookings_tree.column(col, width=widths[col], anchor="w")
        self.bookings_tree.pack(fill="both", expand=True)

        ttk.Label(
            tab,
            text="Estados: pending = pendente de confirmação · confirmed = confirmado pela receção · "
                 "cancelled = recusado/cancelado · incomplete = recolha por terminar",
            foreground="#6b7280",
        ).pack(anchor="w", pady=(6, 0))

        self._refresh_bookings()

    # --------------------------- Configuração --------------------------

    def _build_settings(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Configuração")

        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True, padx=(0, 12), pady=8)

        ttk.Label(left, text="Números autorizados (modo teste) — um por linha").pack(anchor="w")
        self.numbers_text = tk.Text(left, height=6, width=34, font=("Consolas", 9))
        self.numbers_text.pack(anchor="w", pady=(2, 10))
        self.numbers_text.insert("1.0", "\n".join(self.cfg.test_numbers))

        self.respect_schedule = tk.BooleanVar(value=self.cfg.respect_schedule)
        ttk.Checkbutton(
            left, text="Respeitar horário automático (22h30–07h00)",
            variable=self.respect_schedule,
        ).pack(anchor="w")

        hours = ttk.Frame(left)
        hours.pack(anchor="w", pady=4)
        ttk.Label(hours, text="Início").pack(side="left")
        self.start_var = tk.StringVar(value=self.cfg.schedule_start)
        ttk.Entry(hours, textvariable=self.start_var, width=8).pack(side="left", padx=4)
        ttk.Label(hours, text="Fim").pack(side="left")
        self.end_var = tk.StringVar(value=self.cfg.schedule_end)
        ttk.Entry(hours, textvariable=self.end_var, width=8).pack(side="left", padx=4)

        self.debounce_var = tk.IntVar(value=self.cfg.debounce_seconds)
        row = ttk.Frame(left)
        row.pack(anchor="w", pady=4)
        ttk.Label(row, text="Agrupamento de mensagens (segundos)").pack(side="left")
        ttk.Spinbox(row, from_=0, to=60, width=5, textvariable=self.debounce_var).pack(side="left", padx=6)

        ttk.Button(left, text="Guardar configuração", command=self._save_config).pack(anchor="w", pady=(12, 0))

        right = ttk.LabelFrame(tab, text="Pastas")
        right.pack(side="right", fill="y", padx=(0, 0), pady=8)
        for label, path in (
            ("Dados da aplicação", settings.DATA_DIR),
            ("Fotografias (subpasta por T0/T1/T2)", settings.PHOTOS_DIR),
            ("Documentos (.txt/.md)", settings.DOCS_DIR),
            ("Base de dados", settings.DB_PATH),
        ):
            ttk.Label(right, text=label, font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=10, pady=(10, 0))
            ttk.Label(right, text=str(path), wraplength=320, foreground="#374151").pack(anchor="w", padx=10)
        ttk.Button(right, text="Abrir pasta de dados", command=self._open_data_dir).pack(padx=10, pady=12)
        ttk.Button(right, text="Limpar dados com mais de 7 dias", command=self._purge).pack(padx=10, pady=(0, 12))

    # ----------------------------- Registos ----------------------------

    def _build_logs(self) -> None:
        tab = ttk.Frame(self.notebook)
        self.notebook.add(tab, text="Registos")

        ttk.Button(tab, text="Atualizar", command=self._refresh_logs).pack(anchor="w", pady=6)
        self.log_text = tk.Text(tab, wrap="word", state="disabled", font=("Consolas", 9))
        self.log_text.pack(fill="both", expand=True)
        self._refresh_logs()

    # ------------------------------------------------------------------
    # Modos
    # ------------------------------------------------------------------

    def _set_mode(self, mode: str) -> None:
        self.cfg.mode = mode
        self.cfg.save()
        self.store.log_event("INFO", "gui", f"Modo alterado para {mode}")
        self._apply_mode()

    def _emergency(self) -> None:
        self._set_mode(settings.MODE_EMERGENCIA)
        self._system("Emergência ativada: todas as respostas automáticas paradas.")

    def _apply_mode(self) -> None:
        color = STATE_COLORS.get(self.cfg.mode, "#4a5568")
        self.state_label.configure(text=STATE_LABELS.get(self.cfg.mode, self.cfg.mode), bg=color)
        enabled = self.cfg.mode in (settings.MODE_TESTE, settings.MODE_AUTOMATICO)
        self.entry.configure(state="normal" if enabled else "disabled")
        self._refresh_logs()

    # ------------------------------------------------------------------
    # Simulador
    # ------------------------------------------------------------------

    def _append(self, role: str, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M")
        self.chat.configure(state="normal")
        prefix = {"cliente": "Cliente", "bot": "Assistente", "sistema": "Sistema"}[role]
        self.chat.insert("end", f"[{stamp}] {prefix}\n{text}\n\n", role)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _system(self, text: str) -> None:
        self._append("sistema", text)

    def _send(self) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._process(text)

    def _quick(self, prompt: str) -> None:
        self._process(prompt)

    def _process(self, text: str) -> None:
        phone = normalize_phone(self.phone_var.get()) or "+351900000001"
        self._append("cliente", text)

        if "[áudio]" in text.lower():
            outs = self.engine.handle_media(phone, "audio")
        else:
            outs = self.engine.handle(phone, text)

        if not outs:
            reason = self.engine.gate(phone, datetime.now())[1] or "sem resposta"
            self._system(f"Mensagem não respondida ({reason}).")
            return

        self._render(outs)
        self._forward(outs)

    def _render(self, outs: list) -> None:
        self.last_outbound = outs
        for item in outs:
            if item.kind == "image":
                self._append("bot", f"{item.text}\n[{len(item.images)} imagem(ns): "
                                    + ", ".join(Path(p).name for p in item.images) + "]")
            else:
                self._append("bot", item.text)
            if item.meta.get("escalate"):
                self._system("→ conversa marcada para a receção (aguarda intervenção humana).")
            if item.meta.get("booking_id"):
                self._system(f"→ pedido #{item.meta['booking_id']} guardado como PENDENTE.")
                self._refresh_bookings()

    def _forward(self, outs: list) -> None:
        if not (self.auto_forward.get() and self.transport):
            return
        self._dispatch_to_whatsapp(outs)

    def _dispatch_to_whatsapp(self, outs: list) -> None:
        if not self.transport:
            self._system("WhatsApp Web não está aberto.")
            return
        chat = self.target_var.get().strip() if self.send_mode.get() == "por_nome" else ""
        for item in outs:
            if item.kind == "image" and item.images:
                self.transport.send_images(item.images, item.text, chat)
            elif item.text:
                self.transport.send_text(item.text, chat)

    def _send_last(self) -> None:
        if not self.last_outbound:
            self._system("Ainda não há resposta para enviar.")
            return
        if not self.transport:
            self._system("Abra primeiro o WhatsApp Web.")
            return
        self._dispatch_to_whatsapp(self.last_outbound)
        self._system("Resposta enviada para o WhatsApp Web.")

    def _clear_chat(self) -> None:
        self.chat.configure(state="normal")
        self.chat.delete("1.0", "end")
        self.chat.configure(state="disabled")
        self.engine.reset_sessions()

    def _load_transcript(self) -> None:
        phone = normalize_phone(self.phone_var.get() if hasattr(self, "phone_var") else "")
        if not phone:
            return
        rows = self.store.conversation(phone, limit=40)
        if not rows:
            self._system("Simulador pronto. Os preços mostrados são FICTÍCIOS.")
            return
        self._system("Histórico recente desta conversa:")
        for row in rows:
            role = "cliente" if row["direction"] == "in" else "bot"
            self._append(role, row["body"])

    # ------------------------------------------------------------------
    # WhatsApp Web
    # ------------------------------------------------------------------

    def _start_whatsapp(self) -> None:
        if not PLAYWRIGHT_AVAILABLE:
            messagebox.showerror(
                "Playwright em falta",
                "Instale primeiro:\n\n  pip install playwright\n  playwright install chromium",
            )
            return
        if self.transport is None:
            self.transport = WhatsAppWebTransport(
                profile_dir=self.cfg.whatsapp_profile_dir,
                poll_seconds=self.cfg.whatsapp_poll_seconds,
                on_message=self._on_incoming,
                on_event=lambda ev: self.inbox.put(ev),
            )
            self.transport.start()
            self._system("A abrir WhatsApp Web no Chrome… leia o QR se aparecer.")
        else:
            self._system("WhatsApp Web já está a ser gerido por esta janela.")

    def _on_incoming(self, chat: str, text: str) -> None:
        self.inbox.put(("incoming", chat, text))

    def _toggle_poll(self) -> None:
        self.cfg.whatsapp_auto_poll = bool(self.auto_poll.get())
        self.cfg.save()
        if self.transport:
            self.transport.set_auto_poll(self.cfg.whatsapp_auto_poll)

    def _tick(self) -> None:
        try:
            while True:
                item = self.inbox.get_nowait()
                if isinstance(item, Event):
                    self.wa_status.set(item.message)
                elif isinstance(item, tuple) and item and item[0] == "incoming":
                    _, chat, text = item
                    self._append("cliente", f"[WhatsApp: {chat}]\n{text}")
                    outs = self.engine.handle(normalize_phone(chat) or chat, text)
                    self._render(outs)
                    if self.transport:
                        for out in outs:
                            if out.kind == "image" and out.images:
                                self.transport.send_images(out.images, out.text, chat)
                            elif out.text:
                                self.transport.send_text(out.text, chat)
        except queue.Empty:
            pass
        self.after(400, self._tick)

    # ------------------------------------------------------------------
    # Conversas / pedidos / logs
    # ------------------------------------------------------------------

    def _refresh_conversations(self) -> None:
        self.conv_list.delete(0, "end")
        self._conv_map: dict[str, str] = {}
        for row in self.store.conversations():
            label = f"{row['phone']}  ({row['total']})  {row['last_at'][11:16]}"
            self.conv_list.insert("end", label)
            self._conv_map[label] = row["phone"]

    def _on_conversation_select(self, _event=None) -> None:
        selection = self.conv_list.curselection()
        if not selection:
            return
        label = self.conv_list.get(selection[0])
        phone = getattr(self, "_conv_map", {}).get(label, "")
        self.conv_text.configure(state="normal")
        self.conv_text.delete("1.0", "end")
        for row in self.store.conversation(phone, limit=200):
            who = "Cliente" if row["direction"] == "in" else "Assistente"
            self.conv_text.insert("end", f"[{row['created_at']}] {who}:\n{row['body']}\n\n")
        self.conv_text.configure(state="disabled")

    def _refresh_bookings(self) -> None:
        for child in self.bookings_tree.get_children():
            self.bookings_tree.delete(child)
        for row in self.store.bookings():
            self.bookings_tree.insert(
                "", "end",
                values=(
                    row["id"], row["created_at"], row["status"], row["name"],
                    row["villa"], row["checkin"], row["checkout"],
                    f"{row['total']:.2f}", row["phone"],
                ),
            )

    def _set_booking(self, status: str) -> None:
        selection = self.bookings_tree.selection()
        if not selection:
            messagebox.showinfo("Pedidos", "Selecione um pedido primeiro.")
            return
        booking_id = int(self.bookings_tree.item(selection[0], "values")[0])
        self.store.set_booking_status(booking_id, status)
        self.store.log_event("INFO", "gui", f"Pedido #{booking_id} → {status}")
        self._refresh_bookings()
        self._refresh_logs()

    def _export_bookings(self) -> None:
        path = filedialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")],
            initialfile="pedidos_reserva.csv",
        )
        if not path:
            return
        export_bookings_csv(self.store.bookings(), path)
        messagebox.showinfo("Exportação", f"Pedidos exportados para:\n{path}")

    def _refresh_logs(self) -> None:
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        for row in self.store.events(limit=200):
            self.log_text.insert(
                "end", f"[{row['created_at']}] {row['level']:7s} {row['module']:12s} {row['message']}\n"
            )
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------
    # Configuração / utilitários
    # ------------------------------------------------------------------

    def _save_config(self) -> None:
        numbers = [
            normalize_phone(line)
            for line in self.numbers_text.get("1.0", "end").splitlines()
            if line.strip()
        ]
        self.cfg.test_numbers = numbers or list(settings.DEFAULT_TEST_NUMBERS)
        self.cfg.respect_schedule = bool(self.respect_schedule.get())
        self.cfg.schedule_start = self.start_var.get().strip() or "22:30"
        self.cfg.schedule_end = self.end_var.get().strip() or "07:00"
        self.cfg.debounce_seconds = int(self.debounce_var.get())
        self.cfg.simulator_phone = self.phone_var.get().strip()
        self.cfg.whatsapp_send_mode = self.send_mode.get()
        self.cfg.whatsapp_target = self.target_var.get().strip()
        self.cfg.whatsapp_auto_poll = bool(self.auto_poll.get())
        self.cfg.save()
        self.store.log_event("INFO", "gui", "Configuração guardada")
        self._refresh_logs()
        messagebox.showinfo("Configuração", "Configuração guardada.")

    def _save_runtime(self) -> None:
        self.cfg.auto_forward = bool(self.auto_forward.get())
        self.cfg.save()

    def _open_data_dir(self) -> None:
        path = str(settings.DATA_DIR)
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as exc:  # pragma: no cover
            messagebox.showerror("Pasta", f"Não foi possível abrir:\n{path}\n\n{exc}")

    def _purge(self) -> None:
        removed = self.store.purge()
        self.store.log_event("INFO", "gui", f"Purga de retenção: {removed} registo(s)")
        self._refresh_logs()
        messagebox.showinfo("Retenção", f"{removed} registo(s) com mais de 7 dias removidos.")

    def _on_close(self) -> None:
        try:
            if self.transport:
                self.transport.close()
            self.cfg.save()
            self.store.close()
        finally:
            self.destroy()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    app = RecepcionistaApp()
    app.mainloop()
