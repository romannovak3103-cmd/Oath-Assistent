"""HTTP-слой (api): тонкая обёртка над ассистентом и сервисами."""
import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import services, storage
from .assistant import Assistant
from .llm import LLMError, create_provider

log = logging.getLogger("assistant")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

storage.init()
app = FastAPI(title="Voice Assistant")
_assistant = None


def get_assistant():
    """Ассистент создаётся при первом запросе: сервер стартует и без ключа, а ошибка настройки
    приходит в чат понятным текстом."""
    global _assistant
    if _assistant is None:
        try:
            _assistant = Assistant(create_provider())
        except LLMError as e:
            raise HTTPException(500, str(e))
        except Exception as e:  # например, не задан ANTHROPIC_API_KEY
            raise HTTPException(500, f"LLM не настроена: {e}")
    return _assistant


class ChatIn(BaseModel):
    message: str
    session_id: str = "default"


@app.post("/api/chat")
def chat(body: ChatIn, x_user_id: str = Header(default="demo"), assistant: Assistant = Depends(get_assistant)):
    """x-user-id: простая поддержка нескольких пользователей (в проде заменить на авторизацию)."""
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "Пустое сообщение")
    try:
        return {"reply": assistant.chat(x_user_id, body.session_id, text)}
    except LLMError as e:
        raise HTTPException(502, str(e))
    except Exception:
        log.exception("chat failed")
        raise HTTPException(500, "Внутренняя ошибка сервера")


@app.get("/api/items")
def items(x_user_id: str = Header(default="demo")):
    return services.list_all(x_user_id)


@app.get("/api/health")
def health():
    return {"status": "ok"}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
