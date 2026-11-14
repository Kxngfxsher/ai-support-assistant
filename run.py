"""Запуск демо одной командой:  python run.py  [--port 8103] [--reset]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
if hasattr(sys.stdout, "reconfigure"):  # кириллица в консоли Windows на старых версиях Python
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import uvicorn  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.main import create_app  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Ассистент поддержки: демо-сервер")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8103)
    parser.add_argument("--reset", action="store_true", help="пересоздать демо-данные")
    args = parser.parse_args()

    settings = get_settings()
    app = create_app(settings, reset=args.reset)
    service = app.state.service
    engine = f"Claude ({settings.model})" if service.engine.name == "claude" else "правила (без LLM)"
    print(f"Ассистент поддержки · движок: {engine} · разделов в базе знаний: {len(service.sections)}")
    if service.engine_error:
        print(f"  Claude недоступен, работаем на правилах: {service.engine_error}")
    print(f"  Откройте http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
