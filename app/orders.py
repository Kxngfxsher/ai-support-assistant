"""Заказы: поиск по номеру и описание статуса человеческим языком.

В демо заказы лежат в data/orders.json. В реальном проекте этот модуль заменяется запросом
в CMS магазина или CRM: остальной код про источник данных не знает.
"""

import re
from datetime import date, timedelta

from .config import load_json
from .schemas import OrderLookup

MONTHS = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
STATUS_TEXT = {
    "assembling": "собирается на складе. Ориентировочная дата доставки — {date}.",
    "production": "изготавливается на фабрике. Плановая дата доставки — {date}.",
    "shipping": "передан в доставку. Курьер привезёт его {date} и позвонит за час до приезда.",
    "pickup_ready": "готов к выдаче в пункте самовывоза и хранится до {date}. Код получения отправлен в СМС.",
    "delivered": "доставлен {date}.",
    "awaiting_payment": "ожидает оплаты. Оплатить его можно в личном кабинете до {date}, иначе он отменится.",
    "cancelled": "отменён {date}. Если заказ был оплачен, деньги вернутся на карту за 3–5 рабочих дней.",
}
_ORDER_NUMBER_RE = re.compile(r"(?<!\d)\d{5}(?!\d)")


def human_date(moment: date, today: date) -> str:
    delta = (moment - today).days
    if delta == 0:
        return "сегодня"
    if delta == 1:
        return "завтра"
    if delta == -1:
        return "вчера"
    return f"{moment.day} {MONTHS[moment.month - 1]}"


class Orders:
    def __init__(self, records: list[dict] | None = None):
        self.records = {record["number"]: record for record in (records if records is not None else load_json("orders.json"))}

    @staticmethod
    def find_number(text: str) -> str | None:
        """Номер заказа в сообщении: пять цифр подряд."""
        match = _ORDER_NUMBER_RE.search(text)
        return match.group() if match else None

    def lookup(self, number: str, today: date | None = None) -> OrderLookup:
        record = self.records.get(number)
        if record is None:
            return OrderLookup(number=number, found=False, summary=None)
        today = today or date.today()
        when = human_date(today + timedelta(days=record["days"]), today)
        status = STATUS_TEXT[record["status"]].format(date=when)
        # В чат попадает только статус и состав: адрес, телефон и сумма по номеру заказа не раскрываются.
        return OrderLookup(number=number, found=True, summary=f"Заказ №{number} ({record['items']}) {status}")
