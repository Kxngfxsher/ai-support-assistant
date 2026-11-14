"""Канал Telegram: бот на long polling поверх того же сценария, что и чат на сайте.

Запуск: задайте TELEGRAM_BOT_TOKEN в .env и выполните  python telegram_bot.py
Логика покрыта тестами на подменённом HTTP-транспорте; с настоящим ботом в рамках демо не запускалась.
"""

import time

import httpx

from .service import SupportService

API_URL = "https://api.telegram.org"


class TelegramBot:
    def __init__(self, token: str, service: SupportService, client: httpx.Client | None = None):
        self.base_url = f"{API_URL}/bot{token}"
        self.service = service
        self.client = client or httpx.Client(timeout=40.0)
        self.offset = 0

    def call(self, method: str, **params) -> dict:
        response = self.client.post(f"{self.base_url}/{method}", json=params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise RuntimeError(f"Telegram: {payload.get('description', 'ошибка без описания')}")
        return payload

    def poll_once(self, timeout: int = 25) -> int:
        """Один цикл: получить новые сообщения, ответить, отправить ответы операторов."""
        updates = self.call("getUpdates", offset=self.offset, timeout=timeout, allowed_updates=["message"])["result"]
        for update in updates:
            self.offset = update["update_id"] + 1
            self.handle_update(update)
        self.flush_operator_replies()
        return len(updates)

    def handle_update(self, update: dict) -> None:
        message = update.get("message") or {}
        text = (message.get("text") or "").strip()
        chat_id = (message.get("chat") or {}).get("id")
        if not text or chat_id is None:
            return  # стикеры, фото и служебные события ассистент не обрабатывает
        if text == "/start":
            self.call("sendMessage", chat_id=chat_id, text=self.service.company["greeting"])
            return
        result = self.service.handle(text[:2000], channel="telegram", external_id=str(chat_id))
        self.call("sendMessage", chat_id=chat_id, text=result["message"]["text"])

    def flush_operator_replies(self) -> None:
        for row in self.service.store.undelivered("telegram"):
            self.call("sendMessage", chat_id=int(row["external_id"]), text=f"Оператор: {row['text']}")
            self.service.store.mark_delivered(row["id"])

    def run_forever(self) -> None:
        while True:
            try:
                self.poll_once()
            except (httpx.HTTPError, RuntimeError) as exc:
                print(f"Ошибка связи с Telegram: {exc}. Повтор через 5 секунд.")
                time.sleep(5)
