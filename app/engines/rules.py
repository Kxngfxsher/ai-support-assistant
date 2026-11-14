"""Движок правил: поиск по базе знаний и готовый текст раздела в ответ. Работает без LLM.

Понимает вопросы, близкие к формулировкам базы знаний. Перефразированные и составные вопросы
ему даются хуже: это видно в scripts/evaluate.py и служит точкой отсчёта для сравнения с Claude.
"""

import re
import time

from ..kb import Index, Section
from ..schemas import Draft, EngineMeta, OrderLookup, Turn
from . import HANDOFF_TEXT

MIN_COVERAGE = 0.45  # раздел должен покрывать около половины «веса» слов вопроса (подобрано на dev-наборе)

_HUMAN_RE = re.compile(r"оператор|человек|менеджер|сотрудник|специалист|живо(й|го|му)")
_COMPLAINT_RE = re.compile(r"жалоб|пожалова|претензи|хам|нагруби|груб|безобрази|возмутител|роспотребнадзор|в суд|обман")
_ORDER_INTENT_RE = re.compile(r"(где|что с|статус|отследить|когда (привезут|приедет|доставят)).{0,20}заказ")
_GREETING_RE = re.compile(r"^(здравствуйте|добрый (день|вечер)|доброе утро|привет|хай|hello)[\s!.,]*$")
_THANKS_RE = re.compile(r"спасибо|благодар|понятно|ясно|отлично|хорошо")
_BYE_RE = re.compile(r"до свидания|всего доброго|пока$")


def _draft(action: str, answer: str, sources: list[str] | None = None, reason: str | None = None) -> Draft:
    return Draft(action=action, answer=answer, sources=sources or [], escalation_reason=reason)


class RulesEngine:
    name = "rules"

    def __init__(self, company: dict, sections: list[Section]):
        self.company = company
        self.index = Index(sections)

    def reply(self, history: list[Turn], message: str, order: OrderLookup | None) -> tuple[Draft, EngineMeta]:
        started = time.perf_counter()
        draft = self._decide(message, order)
        return draft, EngineMeta(engine="rules", latency_ms=int((time.perf_counter() - started) * 1000))

    def _decide(self, message: str, order: OrderLookup | None) -> Draft:
        text = " ".join(message.lower().replace("ё", "е").split())

        if _COMPLAINT_RE.search(text):
            return _draft("escalate", HANDOFF_TEXT["complaint"], reason="complaint")
        if _HUMAN_RE.search(text) and len(text) < 80:
            return _draft("escalate", HANDOFF_TEXT["human_requested"], reason="human_requested")

        if order is not None:
            if order.found:
                return _draft("answer", order.summary)
            return _draft(
                "clarify",
                f"Не нашёл заказ с номером {order.number}. Проверьте номер: он указан в письме с подтверждением заказа.",
            )
        if _ORDER_INTENT_RE.search(text):
            return _draft("clarify", "Подскажите номер заказа: он состоит из пяти цифр и указан в письме с подтверждением.")

        if _GREETING_RE.match(text):
            return _draft("smalltalk", "Здравствуйте! Спросите меня о доставке, оплате, возврате, гарантии или статусе заказа.")
        if len(text) < 40 and "?" not in text:
            if _BYE_RE.search(text):
                return _draft("smalltalk", "Всего доброго! Будут вопросы — пишите.")
            if _THANKS_RE.search(text):
                return _draft("smalltalk", "Рад помочь! Если появятся ещё вопросы, пишите.")

        hits = self.index.search(message)
        if hits and hits[0].coverage >= MIN_COVERAGE:
            return _draft("answer", hits[0].section.text, sources=[hits[0].section.id])
        return _draft("escalate", HANDOFF_TEXT["no_answer"], reason="no_answer")
