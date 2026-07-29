#!/usr/bin/env python3
"""
Bootstrap .env for local dev from N8N workflow secrets + optional overrides.

Usage:
  python3 scripts/setup_local_env.py
  set -a && source .env && set +a
  uvicorn main:app --reload --port 8080
"""

from __future__ import annotations

import json
import os
import secrets
import string
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"

# Claude settings hold the N8N API key; search common locations.
SETTINGS_CANDIDATES = [
    Path(os.environ["CLAUDE_SETTINGS"]) if os.environ.get("CLAUDE_SETTINGS") else None,
    ROOT / ".claude" / "settings.json",
    ROOT.parent / ".claude" / "settings.json",
    Path.home() / ".claude" / "settings.json",
    Path("/workspace/.claude/settings.json"),
]

BACKOFFICE_URL = "https://qtml5qv6uk.execute-api.eu-central-1.amazonaws.com/production/"
SUPABASE_PROJECT = "ybznbfezrdgzgptxkgul"


def n8n_key() -> str | None:
    """Read N8N API key from env or the first Claude settings file found."""
    if os.environ.get("N8N_API_KEY"):
        return os.environ["N8N_API_KEY"]
    for path in SETTINGS_CANDIDATES:
        if path and path.exists():
            try:
                data = json.loads(path.read_text())
                key = data["mcpServers"]["n8n"]["env"]["N8N_API_KEY"]
                if key:
                    return key
            except (KeyError, json.JSONDecodeError):
                continue
    return None


def fetch_n8n_secrets() -> dict[str, str]:
    """Pull Backoffice + Supabase keys from the live N8N backfill workflow."""
    key = n8n_key()
    if not key:
        print("NOTE: N8N API key not found — fill BACKOFFICE_API_KEY manually.")
        return {}
    req = urllib.request.Request(
        "https://apukuski.app.n8n.cloud/api/v1/workflows/JH2On4vSuJidzbyU",
        headers={"X-N8N-API-KEY": key},
    )
    try:
        wf = json.loads(urllib.request.urlopen(req, timeout=60).read())
    except Exception as e:
        print(f"NOTE: N8N fetch failed ({type(e).__name__}) — fill secrets manually.")
        return {}
    out: dict[str, str] = {}
    for node in wf["nodes"]:
        if node.get("type") != "n8n-nodes-base.httpRequest":
            continue
        params = node.get("parameters", {})
        url = params.get("url", "")
        if "execute-api" in url:
            for h in params.get("headerParameters", {}).get("parameters", []):
                if h.get("name") == "x-api-key" and h.get("value"):
                    out["BACKOFFICE_API_KEY"] = h["value"]
        if "supabase.co" in url and node.get("name", "").startswith("Upsert"):
            for h in params.get("headerParameters", {}).get("parameters", []):
                if h.get("name") == "apikey" and h.get("value"):
                    out["SUPABASE_SERVICE_KEY"] = h["value"]
    return out


def gen_password(n: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def main() -> None:
    existing: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            existing[k.strip()] = v.strip()

    secrets_map = fetch_n8n_secrets()
    lines = []
    if ENV_EXAMPLE.exists():
        for raw in ENV_EXAMPLE.read_text().splitlines():
            if not raw.strip() or raw.strip().startswith("#"):
                lines.append(raw)
                continue
            if "=" not in raw:
                lines.append(raw)
                continue
            key, _, default = raw.partition("=")
            key = key.strip()
            val = existing.get(key, default.strip())
            if key == "BACKOFFICE_API_URL" and not val:
                val = BACKOFFICE_URL
            if key == "BACKOFFICE_API_KEY" and secrets_map.get("BACKOFFICE_API_KEY"):
                val = secrets_map["BACKOFFICE_API_KEY"]
            if key == "DATABASE_URL":
                pw = existing.get("BI_DB_PASSWORD") or gen_password()
                if not val or "PASSWORD" in val:
                    val = (
                        f"postgresql://bi_chatbot_readonly:{pw}"
                        f"@db.{SUPABASE_PROJECT}.supabase.co:5432/postgres"
                    )
                lines.append(f"BI_DB_PASSWORD={pw}")
            lines.append(f"{key}={val}")
    else:
        lines = [f"{k}={v}" for k, v in existing.items()]

    if secrets_map.get("SUPABASE_SERVICE_KEY"):
        lines.append(f"SUPABASE_SERVICE_KEY={secrets_map['SUPABASE_SERVICE_KEY']}")

    ENV_PATH.write_text("\n".join(lines) + "\n")
    print(f"Wrote {ENV_PATH}")
    print("Still required manually: OPENROUTER_API_KEY")
    print("Run Supabase role SQL (docs/INTEGRATION.md) with BI_DB_PASSWORD from .env")


if __name__ == "__main__":
    main()
