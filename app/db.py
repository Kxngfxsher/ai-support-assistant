"""Хранилище на SQLite: диалоги, сообщения и обращения, переданные оператору."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    external_id TEXT,                  -- идентификатор чата во внешнем канале (Telegram chat_id)
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    role TEXT NOT NULL,                -- user | assistant | operator
    text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    action TEXT,                       -- для ответов ассистента: answer | smalltalk | clarify | escalate
    reason TEXT,
    sources_json TEXT NOT NULL DEFAULT '[]',
    meta_json TEXT NOT NULL DEFAULT '{}',
    delivered INTEGER NOT NULL DEFAULT 1  -- 0: ответ оператора ещё не отправлен во внешний канал
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    priority TEXT NOT NULL,            -- high | normal
    question TEXT NOT NULL,
    status TEXT NOT NULL,              -- open | closed
    closed_at TEXT,
    operator_reply TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _all(self, query: str, params: tuple = ()) -> list[dict]:
        with self._conn() as conn:
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def _one(self, query: str, params: tuple = ()) -> dict | None:
        rows = self._all(query, params)
        return rows[0] if rows else None

    # --- диалоги -----------------------------------------------------------------------------

    def create_conversation(self, channel: str, external_id: str | None, at: datetime) -> int:
        with self._conn() as conn:
            return conn.execute(
                "INSERT INTO conversations (channel, external_id, started_at, updated_at) VALUES (?, ?, ?, ?)",
                (channel, external_id, iso(at), iso(at)),
            ).lastrowid

    def get_conversation(self, conversation_id: int) -> dict | None:
        return self._one("SELECT * FROM conversations WHERE id = ?", (conversation_id,))

    def find_conversation(self, channel: str, external_id: str) -> dict | None:
        return self._one(
            "SELECT * FROM conversations WHERE channel = ? AND external_id = ? ORDER BY id DESC LIMIT 1",
            (channel, external_id),
        )

    def conversations(self) -> list[dict]:
        return self._all("SELECT * FROM conversations ORDER BY updated_at DESC, id DESC")

    # --- сообщения ---------------------------------------------------------------------------

    def add_message(
        self,
        conversation_id: int,
        role: str,
        text: str,
        at: datetime,
        *,
        action: str | None = None,
        reason: str | None = None,
        sources: list[str] | None = None,
        meta: dict | None = None,
        delivered: bool = True,
    ) -> int:
        with self._conn() as conn:
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (iso(at), conversation_id))
            return conn.execute(
                "INSERT INTO messages (conversation_id, role, text, created_at, action, reason, sources_json, meta_json, delivered) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    conversation_id,
                    role,
                    text,
                    iso(at),
                    action,
                    reason,
                    json.dumps(sources or [], ensure_ascii=False),
                    json.dumps(meta or {}, ensure_ascii=False),
                    int(delivered),
                ),
            ).lastrowid

    def messages(self, conversation_id: int | None = None) -> list[dict]:
        if conversation_id is None:
            rows = self._all("SELECT * FROM messages ORDER BY id")
        else:
            rows = self._all("SELECT * FROM messages WHERE conversation_id = ? ORDER BY id", (conversation_id,))
        for row in rows:
            row["sources"] = json.loads(row.pop("sources_json"))
            row["meta"] = json.loads(row.pop("meta_json"))
            row["delivered"] = bool(row["delivered"])
        return rows

    def undelivered(self, channel: str) -> list[dict]:
        """Ответы операторов, которые ещё не отправлены во внешний канал."""
        return self._all(
            "SELECT m.id, m.text, c.external_id FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "WHERE m.delivered = 0 AND c.channel = ? ORDER BY m.id",
            (channel,),
        )

    def mark_delivered(self, message_id: int) -> None:
        with self._conn() as conn:
            conn.execute("UPDATE messages SET delivered = 1 WHERE id = ?", (message_id,))

    # --- обращения к оператору ---------------------------------------------------------------

    def create_ticket(self, conversation_id: int, reason: str, priority: str, question: str, at: datetime) -> int:
        with self._conn() as conn:
            return conn.execute(
                "INSERT INTO tickets (conversation_id, created_at, reason, priority, question, status) VALUES (?, ?, ?, ?, ?, 'open')",
                (conversation_id, iso(at), reason, priority, question),
            ).lastrowid

    def get_ticket(self, ticket_id: int) -> dict | None:
        return self._one("SELECT * FROM tickets WHERE id = ?", (ticket_id,))

    def open_ticket(self, conversation_id: int) -> dict | None:
        return self._one(
            "SELECT * FROM tickets WHERE conversation_id = ? AND status = 'open' ORDER BY id DESC LIMIT 1", (conversation_id,)
        )

    def tickets(self) -> list[dict]:
        return self._all("SELECT * FROM tickets ORDER BY status DESC, (priority = 'high') DESC, id DESC")

    def close_ticket(self, ticket_id: int, reply: str, at: datetime) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE tickets SET status = 'closed', closed_at = ?, operator_reply = ? WHERE id = ?",
                (iso(at), reply, ticket_id),
            )

    def clear(self) -> None:
        with self._conn() as conn:
            # Счётчики номеров тоже сбрасываются: после сброса демо диалоги снова нумеруются с 1.
            conn.executescript(
                "DELETE FROM tickets; DELETE FROM messages; DELETE FROM conversations; DELETE FROM sqlite_sequence;"
            )

    def count_conversations(self) -> int:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
