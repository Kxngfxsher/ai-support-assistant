"""Проверка интеграции с SDK Anthropic без сети: HTTP-транспорт подменён заглушкой.

Тесты подтверждают, что в запрос попадают база знаний, история диалога и данные заказа, а ответ
по схеме проходит проверку обоснованности. Реальный вызов API они не заменяют: его проверяет
scripts/evaluate.py --engine claude.
"""

import json

import pytest

anthropic = pytest.importorskip("anthropic")
httpx2 = pytest.importorskip("httpx2")

from app.assistant import Assistant  # noqa: E402
from app.engines import EngineError  # noqa: E402
from app.engines.claude import FALLBACK_BETA, ClaudeEngine  # noqa: E402
from app.orders import Orders  # noqa: E402
from app.schemas import Turn  # noqa: E402


def api_response(payload: dict, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 40, "output_tokens": 90, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 5200},
    }


def make_engine(company, sections, handler) -> ClaudeEngine:
    client = anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )
    return ClaudeEngine(company, sections, model="claude-opus-5-5", effort="low", client=client)


def test_request_contains_knowledge_base_history_and_order(company, sections, rules):
    payload = {"action": "answer", "answer": "Заказ привезут завтра, курьер позвонит за час.", "sources": [], "escalation_reason": None}
    captured = []

    def handler(request):
        captured.append(request)
        return httpx2.Response(200, json=api_response(payload))

    assistant = Assistant(make_engine(company, sections, handler), rules, Orders(), sections)
    history = [
        Turn(role="assistant", text="Здравствуйте!"),  # реплика до первого вопроса клиента: в запрос не попадёт
        Turn(role="user", text="Сколько стоит доставка?"),
        Turn(role="assistant", text="390 ₽, от 5 000 ₽ бесплатно."),
    ]
    reply = assistant.respond(history, "А где мой заказ 10482?")

    body = json.loads(captured[0].content)
    system = body["system"][0]
    assert system["cache_control"] == {"type": "ephemeral"}
    assert '<section id="dostavka/stoimost"' in system["text"] and "390 ₽" in system["text"]
    assert system["text"].count("<section ") == 35
    assert [message["role"] for message in body["messages"]] == ["user", "assistant", "user"]
    assert '<order_lookup number="10482">' in body["messages"][-1]["content"]
    assert body["fallbacks"] == "default" and FALLBACK_BETA in captured[0].headers["anthropic-beta"]
    assert body["output_config"]["effort"] == "low"
    assert set(body["output_config"]["format"]["schema"]["required"]) == {"action", "answer", "sources", "escalation_reason"}

    assert reply.draft.action == "answer" and reply.meta.model == "claude-opus-5-5"
    assert reply.meta.input_tokens == 5240
    assert reply.meta.cost_usd == pytest.approx((40 * 4 + 90 * 20 + 5200 * 0.2) / 1_000_000)


def test_model_answer_with_invented_source_is_not_sent(company, sections, rules):
    payload = {"action": "answer", "answer": "Доставляем дронами за 15 минут.", "sources": ["dostavka/drony"], "escalation_reason": None}
    engine = make_engine(company, sections, lambda request: httpx2.Response(200, json=api_response(payload)))
    reply = Assistant(engine, rules, Orders(), sections).respond([], "Доставляете дронами?")
    assert reply.draft.action == "escalate" and "дронами" not in reply.draft.answer


def test_refusal_and_outage_fall_back_to_rules(company, sections, rules):
    refusal = make_engine(company, sections, lambda request: httpx2.Response(200, json=api_response({}, stop_reason="refusal")))
    with pytest.raises(EngineError):
        refusal.reply([], "Сколько стоит доставка?", None)

    def outage(request):
        return httpx2.Response(529, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})

    reply = Assistant(make_engine(company, sections, outage), rules, Orders(), sections).respond([], "Сколько стоит доставка?")
    assert reply.meta.engine == "rules" and reply.draft.sources == ["dostavka/stoimost"]
    assert "ошибка API 529" in reply.meta.note
