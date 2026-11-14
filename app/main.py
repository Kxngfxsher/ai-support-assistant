"""HTTP API и раздача интерфейса: чат клиента и панель владельца."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, get_settings
from .service import Conflict, NotFound, SupportService

STATIC_DIR = Path(__file__).resolve().parent / "static"


class ChatIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    conversation_id: int | None = None


class OperatorReplyIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


def create_app(settings: Settings | None = None, service: SupportService | None = None, reset: bool = False) -> FastAPI:
    settings = settings or get_settings()
    service = service or SupportService(settings)
    if settings.demo_mode and (reset or service.store.count_conversations() == 0):
        service.seed()

    app = FastAPI(title="Ассистент поддержки", docs_url="/api/docs", redoc_url=None)
    app.state.service = service

    def guarded(action):
        """Переводит ошибки сервисного слоя в HTTP-коды."""
        try:
            return action()
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/config")
    def config() -> dict:
        return {
            "company": service.company["name"],
            "greeting": service.company["greeting"],
            "quick_questions": service.company["quick_questions"],
            "engine": service.engine.name,
            "engine_error": service.engine_error,
            "model": settings.model if service.engine.name == "claude" else None,
            "demo_mode": settings.demo_mode,
            "sections": len(service.sections),
        }

    @app.post("/api/chat")
    def chat(payload: ChatIn) -> dict:
        """Сообщение клиента из виджета на сайте. Тот же сценарий вызывает Telegram-бот."""
        return guarded(lambda: service.handle(payload.text, conversation_id=payload.conversation_id))

    @app.get("/api/conversations")
    def conversations() -> list[dict]:
        return service.conversations()

    @app.get("/api/conversations/{conversation_id}")
    def conversation(conversation_id: int) -> dict:
        return guarded(lambda: service.conversation(conversation_id))

    @app.get("/api/tickets")
    def tickets() -> list[dict]:
        return service.tickets()

    @app.post("/api/tickets/{ticket_id}/reply")
    def operator_reply(ticket_id: int, payload: OperatorReplyIn) -> dict:
        return guarded(lambda: service.operator_reply(ticket_id, payload.text))

    @app.get("/api/stats")
    def stats() -> dict:
        return service.stats()

    @app.get("/api/gaps")
    def gaps() -> list[dict]:
        return service.gaps()

    @app.get("/api/kb")
    def knowledge_base() -> list[dict]:
        return service.knowledge_base()

    if settings.demo_mode:

        @app.post("/api/demo/reset")
        def reset_demo() -> dict:
            return {"seeded": service.seed()}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app
