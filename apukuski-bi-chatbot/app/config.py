"""
config.py — apukuski-bi-chatbot configuration.

Env vars only — never commit secrets. See .env.example.
"""

import logging
import os

log = logging.getLogger("apukuski_bi")

# --- LLM ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "anthropic/claude-sonnet-4.5")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
MAX_TOOL_ITERATIONS = int(os.getenv("MAX_TOOL_ITERATIONS", "8"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1500"))

# --- Supabase Postgres (read-only role recommended) ---
# Direct Postgres connection string to Supabase pooler/session mode.
# Tables: orders, routes, hubs, sync_log (synced from Backoffice via N8N).
DATABASE_URL = os.getenv("DATABASE_URL", "")

# --- Backoffice API (live AWS Lambda — optional fallback / cross-check) ---
BACKOFFICE_API_URL = os.getenv("BACKOFFICE_API_URL", "")
BACKOFFICE_API_KEY = os.getenv("BACKOFFICE_API_KEY", "")
BACKOFFICE_TIMEOUT = float(os.getenv("BACKOFFICE_TIMEOUT", "30.0"))

# --- Training service (knowledge search + /opeta bi) ---
TRAINING_SERVICE_URL = os.getenv("TRAINING_SERVICE_URL", "")
CHIEF_NAME = os.getenv("CHIEF_NAME", "bi")
TRAINING_TIMEOUT = float(os.getenv("TRAINING_TIMEOUT", "20.0"))

# --- Slack bridge (flag_uncertainty escalation) ---
SLACK_BRIDGE_URL = os.getenv("SLACK_BRIDGE_URL", "")
SLACK_BRIDGE_TIMEOUT = float(os.getenv("SLACK_BRIDGE_TIMEOUT", "10.0"))

# --- Session ---
SESSION_MAX_MESSAGES = int(os.getenv("SESSION_MAX_MESSAGES", "24"))

# --- Vault prompts ---
VAULT_DIR = os.getenv("VAULT_DIR", "vault")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")


def log_config_status() -> None:
    checks = [
        ("OPENROUTER_API_KEY", bool(OPENROUTER_API_KEY), True),
        ("DATABASE_URL", bool(DATABASE_URL), True),
        ("BACKOFFICE_API_URL", bool(BACKOFFICE_API_URL), False),
        ("BACKOFFICE_API_KEY", bool(BACKOFFICE_API_KEY), False),
        ("TRAINING_SERVICE_URL", bool(TRAINING_SERVICE_URL), False),
        ("SLACK_BRIDGE_URL", bool(SLACK_BRIDGE_URL), False),
    ]
    for name, ok, required in checks:
        if ok:
            log.info("✅ %s set", name)
        elif required:
            log.warning("⚠️  %s MISSING — core queries won't work", name)
        else:
            log.warning("⚠️  %s not set — integration disabled", name)
