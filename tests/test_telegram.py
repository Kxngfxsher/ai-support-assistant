"""Telegram-канал на подменённом HTTP-транспорте: настоящий Bot API в тестах не вызывается."""

import json

import httpx
import pytest

from app.service import SupportService
from app.telegram import TelegramBot


def update(update_id: int, chat_id: int, text: str | None) -> dict:
    message = {"message_id": update_id, "chat": {"id": chat_id, "type": "private"}}
    if text is not None:
        message["text"] = text
    return {"update_id": update_id, "message": message}


def make_bot(settings, updates: list[dict]):
    sent: list[dict] = []
    polls: list[dict] = []
    queue = [updates]

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.startswith("/botTEST_TOKEN/")
        if request.url.path.endswith("/getUpdates"):
            polls.append(body)
            return httpx.Response(200, json={"ok": True, "result": queue.pop(0) if queue else []})
        sent.append(body)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    service = SupportService(settings)
    bot = TelegramBot("TEST_TOKEN", service, client=httpx.Client(transport=httpx.MockTransport(handler)))
    return bot, service, sent, polls


def test_bot_answers_and_keeps_one_dialog_per_chat(settings):
    updates = [
        update(100, 555, "/start"),
        update(101, 555, "Сколько стоит доставка?"),
        update(102, 555, None),  # стикер или фото: игнорируется
        update(103, 555, "А рассрочка есть?"),
    ]
    bot, service, sent, polls = make_bot(settings, updates)
    assert bot.poll_once() == 4

    assert [message["chat_id"] for message in sent] == [555, 555, 555]
    assert sent[0]["text"] == service.company["greeting"]
    assert "390 ₽" in sent[1]["text"] and "рассрочка" in sent[2]["text"].lower()
    assert bot.offset == 104 and polls[0]["offset"] == 0

    conversations = service.conversations()
    assert len(conversations) == 1 and conversations[0]["channel"] == "telegram"
    assert conversations[0]["messages_count"] == 4

    bot.poll_once()
    assert polls[1]["offset"] == 104  # уже обработанные сообщения повторно не запрашиваются


def test_operator_reply_is_delivered_to_telegram_once(settings):
    bot, service, sent, _ = make_bot(settings, [update(200, 777, "Есть ли у вас подарочные сертификаты?")])
    bot.poll_once()
    assert "оператор" in sent[0]["text"].lower()

    ticket = service.tickets()[0]
    assert ticket["channel"] == "telegram" and ticket["status"] == "open"
    service.operator_reply(ticket["id"], "Сертификатов пока нет.")

    bot.poll_once()
    assert sent[-1] == {"chat_id": 777, "text": "Оператор: Сертификатов пока нет."}
    count = len(sent)
    bot.poll_once()
    assert len(sent) == count  # повторной отправки нет


def test_api_error_is_raised(settings):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "Unauthorized"})

    bot = TelegramBot("TEST_TOKEN", SupportService(settings), client=httpx.Client(transport=httpx.MockTransport(handler)))
    with pytest.raises(RuntimeError, match="Unauthorized"):
        bot.poll_once()
