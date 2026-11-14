"""Модели данных: реплики диалога, ответ движка, сведения о заказе."""

from typing import Literal

from pydantic import BaseModel, Field

Action = Literal["answer", "smalltalk", "clarify", "escalate"]
Reason = Literal["no_answer", "complaint", "human_requested", "other"]

REASON_LABELS: dict[str, str] = {
    "no_answer": "Нет ответа в базе знаний",
    "complaint": "Жалоба или конфликт",
    "human_requested": "Клиент попросил оператора",
    "other": "Нужен человек",
}
CHANNEL_LABELS: dict[str, str] = {"web": "чат на сайте", "telegram": "Telegram"}


class Turn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class OrderLookup(BaseModel):
    """Результат поиска заказа, если клиент назвал номер."""

    number: str
    found: bool
    summary: str | None  # человекочитаемое описание статуса; None, если заказ не найден


class Draft(BaseModel):
    """Ответ движка. Эту схему возвращают оба движка: правила и Claude.

    Все поля обязательные (без значений по умолчанию): так схема подходит для structured outputs.
    """

    action: Action = Field(
        description=(
            "answer — ответ по базе знаний или по данным заказа; smalltalk — приветствие, благодарность, прощание; "
            "clarify — нужен уточняющий вопрос; escalate — передать диалог оператору"
        )
    )
    answer: str = Field(description="Текст сообщения клиенту")
    sources: list[str] = Field(description="Идентификаторы разделов базы знаний, на которых основан ответ")
    escalation_reason: Reason | None = Field(description="Причина передачи оператору; null, если передача не нужна")


class EngineMeta(BaseModel):
    engine: Literal["rules", "claude"]
    model: str | None = None
    latency_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    note: str | None = None
