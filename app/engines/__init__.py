"""Движки ответов. Оба возвращают одну и ту же схему Draft, поэтому взаимозаменяемы."""

from typing import Protocol

from ..kb import Section
from ..schemas import Draft, EngineMeta, OrderLookup, Turn

HANDOFF_TEXT = {
    "no_answer": "В базе знаний нет точного ответа на этот вопрос, поэтому я передаю его оператору. Он ответит здесь, в чате.",
    "complaint": "Мне жаль, что так получилось. Я передаю ваше обращение оператору с пометкой «срочно»: он свяжется с вами в этом чате.",
    "human_requested": "Хорошо, зову оператора. Он ответит в этом чате.",
    "other": "Этот вопрос лучше решит оператор. Я передал ему диалог, он ответит здесь, в чате.",
}


class EngineError(RuntimeError):
    """Движок не смог ответить (нет связи, отказ модели и т. п.)."""


class Engine(Protocol):
    name: str

    def reply(self, history: list[Turn], message: str, order: OrderLookup | None) -> tuple[Draft, EngineMeta]: ...


def build_engine(kind: str, company: dict, sections: list[Section], model: str, effort: str) -> Engine:
    """Создаёт движок по имени. SDK Anthropic импортируется только для режима claude."""
    if kind == "claude":
        from .claude import ClaudeEngine

        return ClaudeEngine(company, sections, model=model, effort=effort)
    from .rules import RulesEngine

    return RulesEngine(company, sections)
