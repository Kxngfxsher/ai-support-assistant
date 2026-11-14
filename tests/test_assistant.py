from datetime import date

import pytest

from app.assistant import Assistant
from app.config import load_json
from app.engines import EngineError
from app.kb import Index, tokens
from app.orders import Orders
from app.schemas import Draft, EngineMeta, Turn


class StubEngine:
    """Движок-заглушка: возвращает заранее заданный ответ (имитация LLM)."""

    name = "claude"

    def __init__(self, draft: Draft | None = None, error: str | None = None):
        self.draft, self.error, self.calls = draft, error, []

    def reply(self, history, message, order):
        self.calls.append((history, message, order))
        if self.error:
            raise EngineError(self.error)
        return self.draft, EngineMeta(engine="claude", model="stub")


def draft(action="answer", answer="Ответ", sources=(), reason=None) -> Draft:
    return Draft(action=action, answer=answer, sources=list(sources), escalation_reason=reason)


# --- база знаний ---------------------------------------------------------------------------------

def test_knowledge_base_is_loaded(sections):
    assert len(sections) == 35
    ids = [section.id for section in sections]
    assert len(set(ids)) == len(ids)
    delivery = next(section for section in sections if section.id == "dostavka/stoimost")
    assert delivery.doc_title == "Доставка" and delivery.title == "Стоимость доставки"
    assert "390 ₽" in delivery.text and "вопросы" not in delivery.text  # подсказки для поиска в ответ не попадают
    assert "сколько стоит доставка" in delivery.aliases


def test_tokens_unify_word_forms_and_synonyms():
    assert tokens("Вернуть возврат верните") == ["возвр", "возвр", "возвр"]
    assert tokens("Привезёте доставку?") == ["доста", "доста"]
    assert tokens("Здравствуйте, подскажите пожалуйста") == []


def test_search_ranks_the_right_section_and_reports_coverage(sections):
    index = Index(sections)
    hits = index.search("Сколько стоит доставка?")
    assert hits[0].section.id == "dostavka/stoimost" and hits[0].coverage == 1.0
    assert index.search("Вы продаёте телевизоры?") == []
    weak = index.search("Можно ли заказать диван в другой обивке?")
    assert not weak or weak[0].coverage < 0.45


# --- движок правил и проверка обоснованности -----------------------------------------------------

def test_rules_pass_dev_set(assistant):
    """Регрессия: правила обязаны проходить набор, на котором настраивались."""
    wrong = []
    for item in load_json("eval_dev.json"):
        reply = assistant.respond([], item["question"])
        expected = item["expected"]
        ok = reply.draft.action == expected["action"]
        if "sources" in expected:
            ok = ok and bool(set(reply.draft.sources) & set(expected["sources"]))
        if not ok:
            wrong.append((item["id"], reply.draft.action, reply.draft.sources))
    assert wrong == []


def test_unknown_question_is_escalated_not_answered(assistant):
    reply = assistant.respond([], "Посоветуйте матрас для больной спины")
    assert reply.draft.action == "escalate" and reply.draft.escalation_reason == "no_answer"
    assert reply.draft.sources == []


def test_complaint_and_human_request(assistant):
    complaint = assistant.respond([], "Ваш курьер нахамил, буду писать в Роспотребнадзор")
    assert (complaint.draft.action, complaint.draft.escalation_reason) == ("escalate", "complaint")
    human = assistant.respond([], "Мне нужен живой человек")
    assert (human.draft.action, human.draft.escalation_reason) == ("escalate", "human_requested")


def test_answer_without_source_is_replaced_by_handoff(rules, sections):
    """Главная защита: ответ «из головы» клиенту не уходит, какой бы движок его ни написал."""
    engine = StubEngine(draft(answer="Доставка на Луну стоит 100 рублей.", sources=[]))
    reply = Assistant(engine, rules, Orders(), sections).respond([], "Сколько стоит доставка на Луну?")
    assert reply.draft.action == "escalate" and reply.draft.escalation_reason == "no_answer"
    assert "Луну" not in reply.draft.answer
    assert "заменён" in reply.guard_note


def test_invented_sources_are_dropped_and_real_ones_kept(rules, sections):
    engine = StubEngine(draft(sources=["dostavka/stoimost", "vydumka/razdel", "dostavka/stoimost"]))
    reply = Assistant(engine, rules, Orders(), sections).respond([], "Сколько стоит доставка?")
    assert reply.draft.action == "answer" and reply.draft.sources == ["dostavka/stoimost"]
    only_fake = StubEngine(draft(sources=["vydumka/razdel"]))
    assert Assistant(only_fake, rules, Orders(), sections).respond([], "?").draft.action == "escalate"


def test_guard_normalizes_other_actions(rules, sections):
    make = lambda d: Assistant(StubEngine(d), rules, Orders(), sections).respond([], "текст").draft  # noqa: E731
    smalltalk = make(draft(action="smalltalk", answer="Пожалуйста!", sources=["dostavka/sroki"], reason="other"))
    assert smalltalk.sources == [] and smalltalk.escalation_reason is None
    silent_escalation = make(draft(action="escalate", answer="  ", reason=None))
    assert silent_escalation.escalation_reason == "other" and silent_escalation.answer
    empty = make(draft(action="clarify", answer=""))
    assert empty.action == "escalate"


def test_llm_failure_falls_back_to_rules(rules, sections):
    engine = StubEngine(error="нет связи с API")
    reply = Assistant(engine, rules, Orders(), sections).respond([], "Сколько стоит доставка?")
    assert reply.meta.engine == "rules" and "нет связи с API" in reply.meta.note
    assert reply.draft.sources == ["dostavka/stoimost"]


# --- заказы --------------------------------------------------------------------------------------

def test_order_lookup_needs_context(assistant):
    with_word = assistant.respond([], "Где мой заказ 10482?")
    assert with_word.order.found and "передан в доставку" in with_word.draft.answer
    assert with_word.draft.action == "answer" and with_word.draft.sources == []
    price = assistant.respond([], "Диван за 54900 ещё есть?")
    assert price.order is None  # число без слова «заказ» номером не считается


def test_order_number_in_the_next_message(assistant):
    first = assistant.respond([], "Где мой заказ?")
    assert first.draft.action == "clarify" and "номер заказа" in first.draft.answer.lower()
    history = [Turn(role="user", text="Где мой заказ?"), Turn(role="assistant", text=first.draft.answer)]
    second = assistant.respond(history, "10485")
    assert second.order.number == "10485" and "изготавливается" in second.draft.answer


def test_unknown_order_and_engine_receives_lookup(assistant, rules, sections):
    missing = assistant.respond([], "Что с заказом 99999?")
    assert missing.draft.action == "clarify" and "99999" in missing.draft.answer
    engine = StubEngine(draft(answer="Заказ в пути."))
    reply = Assistant(engine, rules, Orders(), sections).respond([], "заказ 10487 когда приедет")
    assert engine.calls[0][2].number == "10487" and reply.draft.action == "answer"  # обоснован данными заказа


def test_order_dates_are_relative_to_today():
    orders = Orders()
    today = date(2026, 11, 14)
    assert "завтра" in orders.lookup("10482", today).summary
    assert "сегодня" in orders.lookup("10487", today).summary
    assert "11 ноября" in orders.lookup("10480", today).summary
    assert orders.lookup("00000", today).found is False
    assert Orders.find_number("номер 104821 и 10482") == "10482"


@pytest.mark.parametrize("text", ["Здравствуйте!", "спасибо, всё понятно", "До свидания"])
def test_smalltalk(assistant, text):
    assert assistant.respond([], text).draft.action == "smalltalk"
