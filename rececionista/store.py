"""
rececionista/store.py — Persistência local (SQLite) do harness.

Tabelas: messages, bookings, events, sessions (contexto por número).
Retenção (RF-47): conversas/eventos 7 dias; pedidos de reserva até 30 dias
depois de fechados.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from . import errors
from .models import BookingRecord
from .phones import normalize_phone

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT NOT NULL,
    direction TEXT NOT NULL,
    body TEXT NOT NULL,
    language TEXT DEFAULT '',
    intent TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_phone ON messages(phone, created_at);

CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    phone TEXT, name TEXT, email TEXT, client_phone TEXT,
    checkin TEXT, checkout TEXT, nights INTEGER,
    adults INTEGER, children INTEGER,
    villa TEXT, meal TEXT,
    total REAL, nightly_json TEXT,
    status TEXT DEFAULT 'pending',
    created_at TEXT, updated_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT, module TEXT, message TEXT, created_at TEXT
);

-- Estado da conversa por número (último orçamento, consulta pendente):
-- permite retomar o contexto depois de fechar e reabrir a app.
CREATE TABLE IF NOT EXISTS sessions (
    phone TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Store:
    """Acesso à base de dados; seguro para uso a partir de threads."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    # ------------------------------------------------------------------
    # Mensagens
    # ------------------------------------------------------------------

    def save_message(
        self,
        phone: str,
        direction: str,
        body: str,
        language: str = "",
        intent: str = "",
        created_at: datetime | None = None,
    ) -> None:
        stamp = (created_at or datetime.now()).isoformat(timespec="seconds")
        phone = normalize_phone(phone)
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO messages (phone, direction, body, language, intent, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (phone, direction, body, language, intent, stamp),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            errors.report("store", f"Não foi possível guardar a mensagem ({phone}): {exc}")

    def conversation(self, phone: str, limit: int = 200) -> list[sqlite3.Row]:
        phone = normalize_phone(phone)
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM messages WHERE phone = ? ORDER BY id DESC LIMIT ?",
                (phone, limit),
            )
            rows = cur.fetchall()
        return list(reversed(rows))

    # ------------------------------------------------------------------
    # Estado da conversa (contexto entre arranques)
    # ------------------------------------------------------------------

    def save_session_state(
        self, phone: str, payload: str, at: datetime | None = None
    ) -> None:
        phone = normalize_phone(phone)
        stamp = (at or datetime.now()).isoformat(timespec="seconds")
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO sessions(phone, payload, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT(phone) DO UPDATE SET "
                    "payload = excluded.payload, updated_at = excluded.updated_at",
                    (phone, payload, stamp),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            errors.report("store", f"Não foi possível guardar o contexto ({phone}): {exc}")

    def load_session_state(self, phone: str) -> tuple[str, datetime] | None:
        """Devolve (payload, última atualização), ou None se não existir."""
        phone = normalize_phone(phone)
        with self._lock:
            row = self._conn.execute(
                "SELECT payload, updated_at FROM sessions WHERE phone = ?", (phone,)
            ).fetchone()
        if row is None:
            return None
        try:
            stamp = datetime.fromisoformat(row["updated_at"])
        except ValueError:
            return None
        return str(row["payload"]), stamp

    def clear_session_state(self, phone: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM sessions WHERE phone = ?", (normalize_phone(phone),)
            )
            self._conn.commit()

    def conversations(self) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT phone, COUNT(*) AS total, MAX(created_at) AS last_at"
                " FROM messages GROUP BY phone ORDER BY last_at DESC"
            )
            return cur.fetchall()

    # ------------------------------------------------------------------
    # Pedidos de reserva
    # ------------------------------------------------------------------

    def save_booking(self, record: BookingRecord) -> int:
        now = datetime.now().isoformat(timespec="seconds")
        try:
            return self._save_booking_locked(record, now)
        except sqlite3.Error as exc:
            errors.report(
                "store",
                f"PEDIDO NÃO GRAVADO ({record.name or record.phone}): {exc}",
            )
            return 0

    def _save_booking_locked(self, record: BookingRecord, now: str) -> int:
        with self._lock:
            if record.id:
                self._conn.execute(
                    "UPDATE bookings SET name=?, email=?, client_phone=?, checkin=?,"
                    " checkout=?, nights=?, adults=?, children=?, villa=?, meal=?,"
                    " total=?, nightly_json=?, status=?, updated_at=? WHERE id=?",
                    (
                        record.name, record.email, record.client_phone, record.checkin,
                        record.checkout, record.nights, record.adults, record.children,
                        record.villa, record.meal, record.total, record.nightly_json,
                        record.status, now, record.id,
                    ),
                )
                booking_id = record.id
            else:
                cur = self._conn.execute(
                    "INSERT INTO bookings (phone, name, email, client_phone, checkin,"
                    " checkout, nights, adults, children, villa, meal, total,"
                    " nightly_json, status, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.phone, record.name, record.email, record.client_phone,
                        record.checkin, record.checkout, record.nights, record.adults,
                        record.children, record.villa, record.meal, record.total,
                        record.nightly_json, record.status, now, now,
                    ),
                )
                booking_id = int(cur.lastrowid)
            self._conn.commit()
        return booking_id

    def bookings(self, status: str | None = None) -> list[sqlite3.Row]:
        query = "SELECT * FROM bookings"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY id DESC"
        with self._lock:
            return self._conn.execute(query, params).fetchall()

    def set_booking_status(self, booking_id: int, status: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "UPDATE bookings SET status = ?, updated_at = ? WHERE id = ?",
                    (status, datetime.now().isoformat(timespec="seconds"), booking_id),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            errors.report(
                "store", f"Não foi possível mudar o estado do pedido #{booking_id}: {exc}"
            )

    # ------------------------------------------------------------------
    # Eventos
    # ------------------------------------------------------------------

    def log_event(self, level: str, module: str, message: str) -> None:
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO events (level, module, message, created_at) VALUES (?,?,?,?)",
                    (level, module, message, datetime.now().isoformat(timespec="seconds")),
                )
                self._conn.commit()
        except sqlite3.Error as exc:
            # Não se reporta de volta para o registo (seria um ciclo).
            logging.getLogger("rececionista.store").error(
                "Evento não gravado (%s/%s): %s", level, module, exc
            )

    def events(self, limit: int = 300) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    # ------------------------------------------------------------------
    # Retenção
    # ------------------------------------------------------------------

    def purge(self, days: int = 7, booking_days: int = 30) -> int:
        """Apaga conversas/eventos antigos. Devolve o total de linhas removidas."""
        cutoff = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
        booking_cutoff = (datetime.now() - timedelta(days=booking_days)).isoformat(
            timespec="seconds"
        )
        removed = 0
        try:
            with self._lock:
                for table in ("messages", "events"):
                    cur = self._conn.execute(
                        f"DELETE FROM {table} WHERE created_at < ?", (cutoff,)
                    )
                    removed += cur.rowcount or 0
                cur = self._conn.execute(
                    "DELETE FROM bookings WHERE status IN ('confirmed','cancelled')"
                    " AND updated_at < ?",
                    (booking_cutoff,),
                )
                removed += cur.rowcount or 0
                self._conn.execute("DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
                self._conn.commit()
        except sqlite3.Error as exc:
            errors.report("store", f"A limpeza de dados antigos falhou: {exc}")
            return removed
        return removed

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def export_bookings_csv(rows: Iterable[sqlite3.Row], path: str | Path) -> Path:
    """Exporta pedidos para CSV (a exportação PDF fica para a fase seguinte)."""
    import csv

    path = Path(path)
    fields = [
        "id", "created_at", "status", "name", "email", "client_phone", "checkin",
        "checkout", "nights", "adults", "children", "villa", "meal", "total", "phone",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(fields)
        for row in rows:
            writer.writerow([row[f] if f in row.keys() else "" for f in fields])
    return path
