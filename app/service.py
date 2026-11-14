"""Сценарии поддержки: диалог с клиентом, передача оператору, статистика и пробелы базы знаний."""

from collections import Counter
from datetime import datetime, timedelta

from .assistant import Assistant
from .config import KB_DIR, Settings, load_company, load_json
from .db import Store, utcnow
from .engines import build_engine
from .engines.rules import RulesEngine
from .kb import Section, load_kb
from .orders import Orders
from .schemas import CHANNEL_LABELS, REASON_LABELS, Turn

HISTORY_LIMIT = 12


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    pass


class SupportService:
    def __init__(self, settings: Settings, engine=None):
        self.settings = settings
        self.company = load_company()
        self.sections: list[Section] = load_kb(KB_DIR)
        self.section_by_id = {section.id: section for section in self.sections}
        self.store = Store(settings.db_path)
        self.rules = RulesEngine(self.company, self.sections)
        self.engine_error: str | None = None
        if engine is None:
            engine = self.rules
            if settings.engine == "claude":
                try:
                    engine = build_engine("claude", self.company, self.sections, settings.model, settings.effort)
                except Exception as exc:  # нет SDK или ключа: демо продолжает работать на правилах
                    self.engine_error = f"{type(exc).__name__}: {exc}"
        self.engine = engine
        self.assistant = Assistant(engine, self.rules, Orders(), self.sections)

    # --- диалог ------------------------------------------------------------------------------

    def handle(
        self,
        text: str,
        conversation_id: int | None = None,
        channel: str = "web",
        external_id: str | None = None,
        at: datetime | None = None,
        force_rules: bool = False,
    ) -> dict:
        """Принимает сообщение клиента и возвращает ответ ассистента."""
        text = text.strip()
        if not text:
            raise ValueError("Пустое сообщение")
        at = at or utcnow()
        conversation = self._conversation(conversation_id, channel, external_id, at)
        history = self._history(conversation["id"])
        self.store.add_message(conversation["id"], "user", text, at)

        reply = self.assistant.respond(history, text, force_rules=force_rules)
        draft = reply.draft
        ticket = None
        if draft.action == "escalate":
            ticket = self.store.open_ticket(conversation["id"])
            if ticket is None:
                priority = "high" if draft.escalation_reason == "complaint" else "normal"
                ticket_id = self.store.create_ticket(conversation["id"], draft.escalation_reason, priority, text, at)
                ticket = self.store.get_ticket(ticket_id)

        meta = reply.meta.model_dump()
        if reply.guard_note:
            meta["guard"] = reply.guard_note
        if reply.order:
            meta["order"] = reply.order.number
        message_id = self.store.add_message(
            conversation["id"],
            "assistant",
            draft.answer,
            at + timedelta(seconds=1),
            action=draft.action,
            reason=draft.escalation_reason,
            sources=draft.sources,
            meta=meta,
        )
        message = next(row for row in self.store.messages(conversation["id"]) if row["id"] == message_id)
        return {
            "conversation_id": conversation["id"],
            "message": self._message(message),
            "ticket": self._ticket(ticket) if ticket else None,
        }

    def operator_reply(self, ticket_id: int, text: str, at: datetime | None = None) -> dict:
        """Оператор отвечает клиенту: сообщение попадает в диалог, обращение закрывается."""
        ticket = self.store.get_ticket(ticket_id)
        if ticket is None:
            raise NotFound(f"Обращение {ticket_id} не найдено")
        if ticket["status"] != "open":
            raise Conflict("На это обращение уже ответили")
        text = text.strip()
        if not text:
            raise ValueError("Ответ не может быть пустым")
        at = at or utcnow()
        conversation = self.store.get_conversation(ticket["conversation_id"])
        # В веб-чате ответ подхватит сам виджет; во внешний канал его отправит бот.
        self.store.add_message(conversation["id"], "operator", text, at, delivered=conversation["channel"] == "web")
        self.store.close_ticket(ticket_id, text, at)
        return self._ticket(self.store.get_ticket(ticket_id))

    # --- чтение ------------------------------------------------------------------------------

    def conversation(self, conversation_id: int) -> dict:
        conversation = self.store.get_conversation(conversation_id)
        if conversation is None:
            raise NotFound(f"Диалог {conversation_id} не найден")
        ticket = self.store.open_ticket(conversation_id)
        return {
            **conversation,
            "channel_label": CHANNEL_LABELS.get(conversation["channel"], conversation["channel"]),
            "messages": [self._message(row) for row in self.store.messages(conversation_id)],
            "open_ticket": self._ticket(ticket) if ticket else None,
        }

    def conversations(self) -> list[dict]:
        messages = self.store.messages()
        result = []
        for conversation in self.store.conversations():
            own = [row for row in messages if row["conversation_id"] == conversation["id"]]
            first_question = next((row["text"] for row in own if row["role"] == "user"), "")
            result.append(
                {
                    **conversation,
                    "channel_label": CHANNEL_LABELS.get(conversation["channel"], conversation["channel"]),
                    "preview": first_question,
                    "messages_count": len(own),
                    "escalated": any(row["action"] == "escalate" for row in own),
                }
            )
        return result

    def tickets(self) -> list[dict]:
        return [self._ticket(ticket) for ticket in self.store.tickets()]

    def knowledge_base(self) -> list[dict]:
        return [
            {"id": section.id, "doc": section.doc, "doc_title": section.doc_title, "title": section.title, "text": section.text}
            for section in self.sections
        ]

    def gaps(self) -> list[dict]:
        """Вопросы, на которые в базе знаний не нашлось ответа: подсказка, что в неё дописать."""
        grouped: dict[str, dict] = {}
        last_question: dict[int, dict] = {}
        for row in self.store.messages():
            if row["role"] == "user":
                last_question[row["conversation_id"]] = row
                continue
            question = last_question.get(row["conversation_id"])
            if row["action"] != "escalate" or row["reason"] != "no_answer" or question is None:
                continue
            key = " ".join(question["text"].lower().replace("ё", "е").split()).rstrip("?!. ")
            entry = grouped.setdefault(key, {"question": question["text"], "count": 0, "last_at": question["created_at"]})
            entry["count"] += 1
            entry["last_at"] = max(entry["last_at"], question["created_at"])
        return sorted(grouped.values(), key=lambda entry: (-entry["count"], entry["question"]))

    def stats(self) -> dict:
        messages = self.store.messages()
        tickets = self.store.tickets()
        bot = [row for row in messages if row["role"] == "assistant"]
        resolved = [row for row in bot if row["action"] != "escalate"]
        answers = [row for row in bot if row["action"] == "answer"]
        escalated = len(bot) - len(resolved)
        topics = Counter(
            self.section_by_id[source].doc_title for row in answers for source in row["sources"] if source in self.section_by_id
        )
        topics.update({"Статус заказа": sum(1 for row in answers if row["meta"].get("order"))})
        latencies = [row["meta"].get("latency_ms", 0) for row in bot]
        return {
            "conversations": self.store.count_conversations(),
            "questions": sum(row["role"] == "user" for row in messages),
            "resolved_by_bot": len(resolved),
            "escalated": escalated,
            "deflection_rate": round(len(resolved) / len(bot), 2) if bot else None,
            "open_tickets": sum(ticket["status"] == "open" for ticket in tickets),
            "urgent_tickets": sum(ticket["status"] == "open" and ticket["priority"] == "high" for ticket in tickets),
            "gaps": len(self.gaps()),
            "avg_latency_ms": round(sum(latencies) / len(latencies)) if latencies else 0,
            "time_saved_minutes": round(len(answers) * self.settings.manual_minutes_per_question),
            "manual_minutes_per_question": self.settings.manual_minutes_per_question,
            "llm_cost_usd": round(sum(row["meta"].get("cost_usd") or 0 for row in bot), 4),
            "topics": [{"topic": topic, "count": count} for topic, count in topics.most_common() if count],
        }

    # --- демо --------------------------------------------------------------------------------

    def seed(self) -> int:
        """Заполняет базу историей диалогов, чтобы панель владельца не была пустой."""
        self.store.clear()
        now = utcnow()
        dialogs = load_json("seed_dialogs.json")
        for dialog in sorted(dialogs, key=lambda entry: -entry["minutes_ago"]):
            at = now - timedelta(minutes=dialog["minutes_ago"])
            conversation_id = None
            external_id = f"demo-{dialog['minutes_ago']}" if dialog["channel"] != "web" else None
            result = None
            for index, text in enumerate(dialog["turns"]):
                result = self.handle(
                    text,
                    conversation_id=conversation_id,
                    channel=dialog["channel"],
                    external_id=external_id,
                    at=at + timedelta(minutes=index),
                    force_rules=True,
                )
                conversation_id = result["conversation_id"]
            if dialog.get("operator_reply") and result and result["ticket"]:
                self.operator_reply(result["ticket"]["id"], dialog["operator_reply"], at + timedelta(minutes=25))
                for row in self.store.undelivered(dialog["channel"]):
                    self.store.mark_delivered(row["id"])
        return len(dialogs)

    # --- внутреннее --------------------------------------------------------------------------

    def _conversation(self, conversation_id: int | None, channel: str, external_id: str | None, at: datetime) -> dict:
        if conversation_id is not None:
            conversation = self.store.get_conversation(conversation_id)
            if conversation is None:
                raise NotFound(f"Диалог {conversation_id} не найден")
            return conversation
        if external_id is not None:
            conversation = self.store.find_conversation(channel, external_id)
            if conversation is not None:
                return conversation
        return self.store.get_conversation(self.store.create_conversation(channel, external_id, at))

    def _history(self, conversation_id: int) -> list[Turn]:
        rows = self.store.messages(conversation_id)[-HISTORY_LIMIT:]
        return [Turn(role="user" if row["role"] == "user" else "assistant", text=row["text"]) for row in rows]

    def _message(self, row: dict) -> dict:
        sources = [
            {"id": source, "title": self.section_by_id[source].title, "doc_title": self.section_by_id[source].doc_title}
            for source in row["sources"]
            if source in self.section_by_id
        ]
        return {
            "id": row["id"],
            "role": row["role"],
            "text": row["text"],
            "created_at": row["created_at"],
            "action": row["action"],
            "reason": row["reason"],
            "sources": sources,
            "meta": row["meta"],
        }

    def _ticket(self, ticket: dict) -> dict:
        conversation = self.store.get_conversation(ticket["conversation_id"])
        return {
            **ticket,
            "reason_label": REASON_LABELS.get(ticket["reason"], ticket["reason"]),
            "channel": conversation["channel"],
            "channel_label": CHANNEL_LABELS.get(conversation["channel"], conversation["channel"]),
        }
