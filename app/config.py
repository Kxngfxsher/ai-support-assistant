"""Настройки приложения: переменные окружения и необязательный файл .env."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
KB_DIR = DATA_DIR / "kb"
DEFAULT_MODEL = "claude-opus-5-5"


def load_dotenv(path: Path) -> None:
    """Минимальный загрузчик .env (KEY=VALUE). Уже заданные переменные не перезаписывает."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _flag(name: str, default: bool) -> bool:
    return os.getenv(name, "1" if default else "0").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    engine: str  # фактический движок: rules | claude
    engine_requested: str
    model: str
    effort: str
    db_path: Path
    demo_mode: bool
    manual_minutes_per_question: float
    telegram_bot_token: str | None


def get_settings() -> Settings:
    load_dotenv(BASE_DIR / ".env")
    requested = os.getenv("ENGINE", "auto").strip().lower()
    if requested not in ("auto", "rules", "claude"):
        requested = "auto"
    has_credentials = bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))
    engine = requested if requested != "auto" else ("claude" if has_credentials else "rules")
    return Settings(
        engine=engine,
        engine_requested=requested,
        model=os.getenv("CLAUDE_MODEL", DEFAULT_MODEL).strip(),
        effort=os.getenv("CLAUDE_EFFORT", "low").strip().lower(),
        db_path=Path(os.getenv("SUPPORT_DB", str(BASE_DIR / "var" / "support.db"))),
        demo_mode=_flag("DEMO_MODE", True),
        manual_minutes_per_question=float(os.getenv("MANUAL_MINUTES_PER_QUESTION", "3")),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN") or None,
    )


def load_json(name: str):
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def load_company() -> dict:
    return load_json("company.json")
