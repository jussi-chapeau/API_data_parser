"""
llm.py — OpenRouter + agentic tool loop + vault system prompt.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

from openai import AsyncOpenAI

from app.config import (
    LLM_MAX_TOKENS,
    MAX_TOOL_ITERATIONS,
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    VAULT_DIR,
)
from app.tools import TOOL_IMPLEMENTATIONS, TOOLS_SCHEMA

log = logging.getLogger("apukuski_bi.llm")

client = AsyncOpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_BASE_URL)

SAGE_ORDER = ["SOUL.md", "IDENTITY.md", "COMPANY.md", "BI.md", "TOOLS.md"]

_FI_WEEKDAYS = [
    "maanantai", "tiistai", "keskiviikko", "torstai",
    "perjantai", "lauantai", "sunnuntai",
]


def _now_helsinki_line() -> str:
    now = datetime.now(ZoneInfo("Europe/Helsinki"))
    wd = _FI_WEEKDAYS[now.weekday()]
    return (
        f"Tänään on {wd} {now.day}.{now.month}.{now.year}, kello "
        f"{now.strftime('%H:%M')} (Suomen aikaa). Kun kysymyksessä on "
        f"suhteellinen aika ('viime viikko', 'eilen', 'tässä kuussa'), laske "
        f"tarkat päivämäärät tästä päivästä ja käytä niitä toolien "
        f"parametreissa."
    )


def build_system_prompt() -> str:
    parts = [_now_helsinki_line()]
    vault = Path(VAULT_DIR)
    for fname in SAGE_ORDER:
        f = vault / fname
        if f.exists():
            parts.append(f.read_text(encoding="utf-8"))
        else:
            log.warning("vault file missing: %s", fname)
    return "\n\n---\n\n".join(parts)


async def generate_reply(messages: List[Dict[str, Any]]) -> str:
    chat: List[Dict[str, Any]] = [
        {"role": "system", "content": build_system_prompt()}
    ] + messages

    for iteration in range(MAX_TOOL_ITERATIONS):
        response = await client.chat.completions.create(
            model=OPENROUTER_MODEL,
            messages=chat,
            tools=TOOLS_SCHEMA,
            tool_choice="auto",
            temperature=0.3,
            max_tokens=LLM_MAX_TOKENS,
        )
        msg = response.choices[0].message

        if not msg.tool_calls:
            return msg.content or "(tyhjä vastaus)"

        chat.append({
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in msg.tool_calls
            ],
        })

        for tc in msg.tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            impl = TOOL_IMPLEMENTATIONS.get(name)
            if impl is None:
                result = json.dumps({"error": f"tuntematon tooli: {name}"}, ensure_ascii=False)
            else:
                try:
                    result = await impl(**args)
                except TypeError as e:
                    result = json.dumps({"error": f"väärät parametrit: {e}"}, ensure_ascii=False)
                except Exception as e:
                    log.exception("tool %s failed", name)
                    result = json.dumps({"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False)
            chat.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": str(result)[:12000],
            })
        log.info("tool round %s: %s", iteration + 1, [tc.function.name for tc in msg.tool_calls])

    return (
        "Analyysi vaati liian monta työkalukierrosta — tarkenna kysymystä "
        "tai pilko se osiin."
    )
