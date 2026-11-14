"""Запуск Telegram-бота:  python telegram_bot.py   (нужен TELEGRAM_BOT_TOKEN в .env)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_settings  # noqa: E402
from app.service import SupportService  # noqa: E402
from app.telegram import TelegramBot  # noqa: E402


def main() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise SystemExit("Задайте TELEGRAM_BOT_TOKEN в файле .env (токен выдаёт @BotFather).")
    service = SupportService(settings)
    print(f"Бот запущен. Движок: {service.engine.name}. Остановить: Ctrl+C")
    try:
        TelegramBot(settings.telegram_bot_token, service).run_forever()
    except KeyboardInterrupt:
        print("Бот остановлен.")


if __name__ == "__main__":
    main()
