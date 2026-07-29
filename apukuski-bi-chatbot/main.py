"""
main.py — apukuski-bi-chatbot FastAPI service (Bertta).

Endpoints (slack-bridge compatible):
  GET  /            service info
  GET  /health      health check
  POST /chat        {message, session_id?, channel?} → {reply}

Session memory: in-memory per session_id (v0.1). See docs/INTEGRATION.md for Redis.
"""

import logging
from contextlib import asynccontextmanager
from typing import Dict, List

from fastapi import FastAPI
from pydantic import BaseModel

from app.config import LOG_LEVEL, OPENROUTER_MODEL, SESSION_MAX_MESSAGES, log_config_status
from app.llm import generate_reply

logging.basicConfig(level=LOG_LEVEL)
log = logging.getLogger("apukuski_bi")

_sessions: Dict[str, List[dict]] = {}


def _load_history(session_id: str) -> List[dict]:
    return list(_sessions.get(session_id, []))


def _save_history(session_id: str, history: List[dict]) -> None:
    _sessions[session_id] = history[-SESSION_MAX_MESSAGES:]


@asynccontextmanager
async def lifespan(app: FastAPI):
    log_config_status()
    log.info("apukuski-bi-chatbot started")
    yield


app = FastAPI(title="apukuski-bi-chatbot", lifespan=lifespan)


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    channel: str = "operator"


class ChatResponse(BaseModel):
    reply: str
    session_id: str


@app.get("/")
async def root():
    return {"service": "apukuski-bi-chatbot", "agent": "Bertta", "model": OPENROUTER_MODEL}


@app.get("/health")
async def health():
    return {"ok": True, "service": "apukuski-bi-chatbot"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    history = _load_history(req.session_id)
    history.append({"role": "user", "content": req.message})

    try:
        reply = await generate_reply(history)
    except Exception:
        log.exception("chat failed")
        reply = (
            "Analyysi epäonnistui — kokeile uudelleen tai tarkenna kysymystä. "
            "Jos ongelma jatkuu, tarkista palvelun konfiguraatio."
        )

    history.append({"role": "assistant", "content": reply})
    _save_history(req.session_id, history)
    return ChatResponse(reply=reply, session_id=req.session_id)
