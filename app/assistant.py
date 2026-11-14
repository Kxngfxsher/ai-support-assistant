"""Логика одного ответа: данные заказа → движок → проверка обоснованности.

Проверка выполняется обычным кодом после любого движка: ответ «по существу» обязан ссылаться
на существующий раздел базы знаний или на найденный заказ. Иначе вместо него клиент получает
сообщение о передаче оператору. Так ассистент не может уверенно ответить «из головы».
"""

from dataclasses import dataclass

from .engines import HANDOFF_TEXT, Engine, EngineError
from .kb import Section
from .orders import Orders
from .schemas import Draft, EngineMeta, OrderLookup, Turn


@dataclass
class Reply:
    draft: Draft
    meta: EngineMeta
    order: OrderLookup | None
    guard_note: str | None


class Assistant:
    def __init__(self, engine: Engine, fallback: Engine, orders: Orders, sections: list[Section]):
        self.engine = engine
        self.fallback = fallback
        self.orders = orders
        self.section_ids = {section.id for section in sections}

    def respond(self, history: list[Turn], message: str, force_rules: bool = False) -> Reply:
        order = self._order_context(history, message)
        engine = self.fallback if force_rules else self.engine
        try:
            draft, meta = engine.reply(history, message, order)
        except EngineError as exc:
            draft, meta = self.fallback.reply(history, message, order)
            meta.note = f"LLM недоступна ({exc}); ответ подготовлен правилами"
        draft, guard_note = self._guard(draft, order)
        return Reply(draft=draft, meta=meta, order=order, guard_note=guard_note)

    def _order_context(self, history: list[Turn], message: str) -> OrderLookup | None:
        """Ищет заказ, если клиент назвал номер. Данные добавляются в запрос до обращения к движку."""
        number = self.orders.find_number(message)
        if number is None:
            return None
        asked_before = bool(history) and history[-1].role == "assistant" and "номер заказа" in history[-1].text.lower()
        if "заказ" in message.lower() or asked_before:
            return self.orders.lookup(number)
        return None

    def _guard(self, draft: Draft, order: OrderLookup | None) -> tuple[Draft, str | None]:
        sources = [source for source in dict.fromkeys(draft.sources) if source in self.section_ids]
        action, answer, reason = draft.action, draft.answer.strip(), draft.escalation_reason
        note = None

        grounded = bool(sources) or (order is not None and order.found)
        if action == "answer" and not grounded:
            action, reason, answer = "escalate", "no_answer", HANDOFF_TEXT["no_answer"]
            note = "Ответ без опоры на базу знаний заменён передачей оператору"
        if action != "escalate" and not answer:
            action, reason = "escalate", "other"
            note = "Пустой ответ заменён передачей оператору"

        if action == "escalate":
            reason = reason or "other"
            answer = answer or HANDOFF_TEXT[reason]
            sources = []
        else:
            reason = None
            if action != "answer":
                sources = []
        return Draft(action=action, answer=answer, sources=sources, escalation_reason=reason), note
