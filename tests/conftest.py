import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assistant import Assistant  # noqa: E402
from app.config import KB_DIR, Settings, load_company  # noqa: E402
from app.engines.rules import RulesEngine  # noqa: E402
from app.kb import load_kb  # noqa: E402
from app.orders import Orders  # noqa: E402


def make_settings(db_path: Path, **overrides) -> Settings:
    values = dict(
        engine="rules",
        engine_requested="rules",
        model="claude-opus-5-5",
        effort="low",
        db_path=db_path,
        demo_mode=True,
        manual_minutes_per_question=3.0,
        telegram_bot_token=None,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return make_settings(tmp_path / "test.db")


@pytest.fixture(scope="session")
def company() -> dict:
    return load_company()


@pytest.fixture(scope="session")
def sections():
    return load_kb(KB_DIR)


@pytest.fixture
def rules(company, sections) -> RulesEngine:
    return RulesEngine(company, sections)


@pytest.fixture
def assistant(rules, sections) -> Assistant:
    return Assistant(rules, rules, Orders(), sections)


@pytest.fixture
def client(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app(settings)) as test_client:
        yield test_client
