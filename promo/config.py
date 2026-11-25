"""Carrega settings.yaml e variáveis de ambiente (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv é opcional
    def load_dotenv(*_a, **_k):
        return False

ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Secrets:
    tg_api_id: int | None = None
    tg_api_hash: str | None = None
    tg_session: str = "data/promo_monitor"
    db_path: str = "data/promo_monitor.db"
    bot_token: str | None = None
    alert_chat_id: str | None = None
    supabase_url: str | None = None
    supabase_service_key: str | None = None
    hasdata_api_key: str | None = None
    reef_api_key: str | None = None
    meli_access_token: str | None = None
    anthropic_api_key: str | None = None


@dataclass
class Settings:
    raw: dict[str, Any]
    secrets: Secrets = field(default_factory=Secrets)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node


def load_settings(path: str | Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    cfg_path = Path(path) if path else ROOT / "config" / "settings.yaml"
    with open(cfg_path, encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    def env(name: str) -> str | None:
        value = os.getenv(name)
        return value.strip() if value and value.strip() else None

    api_id = env("TG_API_ID")
    secrets = Secrets(
        tg_api_id=int(api_id) if api_id else None,
        tg_api_hash=env("TG_API_HASH"),
        tg_session=env("TG_SESSION") or "data/promo_monitor",
        db_path=env("DB_PATH") or "data/promo_monitor.db",
        bot_token=env("TG_BOT_TOKEN"),
        alert_chat_id=env("TG_ALERT_CHAT_ID"),
        supabase_url=env("SUPABASE_URL"),
        supabase_service_key=env("SUPABASE_SERVICE_ROLE_KEY"),
        hasdata_api_key=env("HASDATA_API_KEY"),
        reef_api_key=env("REEF_API_KEY"),
        meli_access_token=env("MELI_ACCESS_TOKEN"),
        anthropic_api_key=env("ANTHROPIC_API_KEY"),
    )
    return Settings(raw=raw, secrets=secrets)
