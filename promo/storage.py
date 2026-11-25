"""Persistência: Supabase (produção) ou SQLite local (testes / sem internet).

O Supabase é acessado pela API REST (PostgREST) com a chave service_role,
então não precisa do pacote `supabase`. Nunca coloque essa chave no painel.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from .models import Analysis, Offer, Quote


def analysis_row(a: Analysis, offer_id: str) -> dict[str, Any]:
    best = a.best
    return {
        "offer_id": offer_id,
        "market_median": a.market_median,
        "market_min": a.market_min,
        "n_quotes": len(a.accepted_quotes),
        "best_channel": best.channel if best else None,
        "sale_price": best.sale_price if best else None,
        "total_cost": a.total_cost,
        "net_revenue": best.net_revenue if best else None,
        "profit": best.profit if best else None,
        "roi": best.roi if best else None,
        "is_profitable": a.is_profitable,
        "confidence": a.confidence,
        "details": {
            "channels": [asdict(c) for c in a.channels],
            "reasons": a.reasons,
            "profile": a.profile,
        },
    }


def quote_rows(quotes: list[Quote], offer_id: str) -> list[dict[str, Any]]:
    rows = []
    for q in quotes:
        row = {"offer_id": offer_id, **asdict(q)}
        # A condição estruturada fica no cache JSON e no perfil da análise.
        # O título também a identifica no painel, sem exigir migração de tabela.
        row.pop("condition", None)
        rows.append(row)
    return rows


class Repository(Protocol):
    async def seen(self, chat_id: int | None, message_id: int | None) -> bool: ...
    async def save_offer(self, offer: Offer) -> str | None: ...
    async def save_analysis(self, analysis: Analysis, offer_id: str) -> str: ...
    async def save_alert(self, message: str, analysis_id: str | None = None,
                         watch_id: str | None = None) -> None: ...
    async def list_watchlist(self) -> list[dict[str, Any]]: ...
    async def record_watch_price(self, watch_id: str, price: float, store: str | None,
                                 url: str | None) -> None: ...
    async def recent_alert_for_watch(self, watch_id: str, hours: int = 12) -> bool: ...
    async def remote_rules(self) -> tuple[dict | None, list[dict]]: ...
    async def sync_categories(self, rows: list[dict]) -> None: ...
    async def get_cache(self, key: str) -> dict | None: ...
    async def put_cache(self, key: str, query: str, provider: str, quotes: list[dict]) -> None: ...
    async def paid_searches_since(self, provider: str, since_iso: str) -> int: ...


class SupabaseRepository:
    def __init__(self, url: str, service_key: str):
        import httpx

        self.base = url.rstrip("/") + "/rest/v1"
        self.client = httpx.AsyncClient(headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Content-Type": "application/json",
        }, timeout=30)

    async def _get(self, table: str, params: dict[str, str]) -> list[dict]:
        r = await self.client.get(f"{self.base}/{table}", params=params)
        r.raise_for_status()
        return r.json()

    async def _insert(self, table: str, rows: Any, *, ignore_dupes: bool = False) -> list[dict]:
        prefer = "return=representation"
        if ignore_dupes:
            prefer += ",resolution=ignore-duplicates"
        r = await self.client.post(f"{self.base}/{table}", json=rows, headers={"Prefer": prefer})
        r.raise_for_status()
        return r.json()

    async def seen(self, chat_id, message_id) -> bool:
        if chat_id is None or message_id is None:
            return False
        rows = await self._get("offers", {"chat_id": f"eq.{chat_id}", "message_id": f"eq.{message_id}",
                                          "select": "id", "limit": "1"})
        return bool(rows)

    async def save_offer(self, offer: Offer) -> str | None:
        rows = await self._insert("offers", offer.to_row(), ignore_dupes=True)
        return rows[0]["id"] if rows else None

    async def save_analysis(self, analysis: Analysis, offer_id: str) -> str:
        if analysis.quotes:
            await self._insert("market_quotes", quote_rows(analysis.quotes, offer_id))
        rows = await self._insert("analyses", analysis_row(analysis, offer_id))
        return rows[0]["id"]

    async def save_alert(self, message, analysis_id=None, watch_id=None) -> None:
        await self._insert("alerts", {"message": message, "analysis_id": analysis_id,
                                      "watch_id": watch_id})

    async def list_watchlist(self) -> list[dict]:
        return await self._get("watchlist", {"active": "eq.true", "select": "*"})

    async def record_watch_price(self, watch_id, price, store, url) -> None:
        now = datetime.now(timezone.utc).isoformat()
        await self._insert("price_history", {"watch_id": watch_id, "price": price,
                                             "store": store, "url": url})
        r = await self.client.patch(f"{self.base}/watchlist", params={"id": f"eq.{watch_id}"},
                                    json={"last_price": price, "last_store": store,
                                          "last_checked_at": now})
        r.raise_for_status()

    async def recent_alert_for_watch(self, watch_id, hours=12) -> bool:
        since = datetime.now(timezone.utc).timestamp() - hours * 3600
        since_iso = datetime.fromtimestamp(since, timezone.utc).isoformat()
        rows = await self._get("alerts", {"watch_id": f"eq.{watch_id}",
                                          "sent_at": f"gte.{since_iso}", "select": "id", "limit": "1"})
        return bool(rows)

    async def remote_rules(self) -> tuple[dict | None, list[dict]]:
        """Regras e grupos editados no painel (tabelas app_settings e rule_groups)."""
        settings = await self._get("app_settings", {"id": "eq.1", "select": "rules"})
        groups = await self._get("rule_groups", {"select": "*", "order": "created_at"})
        return (settings[0]["rules"] if settings else None), groups

    async def sync_categories(self, rows: list[dict]) -> None:
        r = await self.client.post(f"{self.base}/catalog_categories", json=rows,
                                   headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        r.raise_for_status()

    # --- cache das buscas pagas (segunda opinião) ---
    async def get_cache(self, key: str) -> dict | None:
        rows = await self._get("market_cache", {"key": f"eq.{key}", "select": "*", "limit": "1"})
        return rows[0] if rows else None

    async def put_cache(self, key: str, query: str, provider: str, quotes: list[dict]) -> None:
        r = await self.client.post(f"{self.base}/market_cache", json={
            "key": key, "query": query, "provider": provider, "quotes": quotes,
            "created_at": datetime.now(timezone.utc).isoformat()},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"})
        r.raise_for_status()

    async def paid_searches_since(self, provider: str, since_iso: str) -> int:
        r = await self.client.get(f"{self.base}/market_cache", params={
            "provider": f"eq.{provider}", "created_at": f"gte.{since_iso}", "select": "key"},
            headers={"Prefer": "count=exact", "Range": "0-0"})
        r.raise_for_status()
        total = r.headers.get("content-range", "*/0").split("/")[-1]
        return int(total) if total.isdigit() else 0


class SQLiteRepository:
    """Mesma interface, num arquivo local. Útil para testar sem Supabase."""

    def __init__(self, path: str | Path = "data/promo_monitor.db"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            create table if not exists offers (id text primary key, data text, chat_id integer,
                message_id integer, created_at text, unique(chat_id, message_id));
            create table if not exists analyses (id text primary key, offer_id text, data text,
                created_at text);
            create table if not exists market_quotes (id text primary key, offer_id text, data text);
            create table if not exists alerts (id text primary key, analysis_id text, watch_id text,
                message text, sent_at text);
            create table if not exists watchlist (id text primary key, name text, query text, url text,
                target_price real, min_profit real, active integer default 1, last_price real,
                last_store text, last_checked_at text);
            create table if not exists price_history (id text primary key, watch_id text, price real,
                store text, url text, checked_at text);
        """)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def seen(self, chat_id, message_id) -> bool:
        if chat_id is None or message_id is None:
            return False
        cur = self.db.execute("select 1 from offers where chat_id=? and message_id=?",
                              (chat_id, message_id))
        return cur.fetchone() is not None

    async def save_offer(self, offer: Offer) -> str | None:
        oid = str(uuid.uuid4())
        try:
            self.db.execute("insert into offers values (?,?,?,?,?)",
                            (oid, json.dumps(offer.to_row(), default=str), offer.chat_id,
                             offer.message_id, self._now()))
        except sqlite3.IntegrityError:
            return None
        self.db.commit()
        return oid

    async def save_analysis(self, analysis: Analysis, offer_id: str) -> str:
        for row in quote_rows(analysis.quotes, offer_id):
            self.db.execute("insert into market_quotes values (?,?,?)",
                            (str(uuid.uuid4()), offer_id, json.dumps(row, default=str)))
        aid = str(uuid.uuid4())
        self.db.execute("insert into analyses values (?,?,?,?)",
                        (aid, offer_id, json.dumps(analysis_row(analysis, offer_id), default=str),
                         self._now()))
        self.db.commit()
        return aid

    async def save_alert(self, message, analysis_id=None, watch_id=None) -> None:
        self.db.execute("insert into alerts values (?,?,?,?,?)",
                        (str(uuid.uuid4()), analysis_id, watch_id, message, self._now()))
        self.db.commit()

    async def list_watchlist(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("select * from watchlist where active=1")]

    async def record_watch_price(self, watch_id, price, store, url) -> None:
        now = self._now()
        self.db.execute("insert into price_history values (?,?,?,?,?,?)",
                        (str(uuid.uuid4()), watch_id, price, store, url, now))
        self.db.execute("update watchlist set last_price=?, last_store=?, last_checked_at=? where id=?",
                        (price, store, now, watch_id))
        self.db.commit()

    async def recent_alert_for_watch(self, watch_id, hours=12) -> bool:
        since = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - hours * 3600,
                                       timezone.utc).isoformat()
        cur = self.db.execute("select 1 from alerts where watch_id=? and sent_at>=?", (watch_id, since))
        return cur.fetchone() is not None

    async def remote_rules(self) -> tuple[dict | None, list[dict]]:
        return None, []  # sem painel: só o settings.yaml

    async def sync_categories(self, rows: list[dict]) -> None:
        return None

    async def get_cache(self, key: str) -> dict | None:
        return getattr(self, "_cache", {}).get(key)

    async def put_cache(self, key: str, query: str, provider: str, quotes: list[dict]) -> None:
        if not hasattr(self, "_cache"):
            self._cache = {}
        self._cache[key] = {"key": key, "query": query, "provider": provider, "quotes": quotes,
                            "created_at": self._now()}

    async def paid_searches_since(self, provider: str, since_iso: str) -> int:
        return sum(1 for c in getattr(self, "_cache", {}).values()
                   if c["provider"] == provider and c["created_at"] >= since_iso)


def build_repository(settings) -> Repository:
    s = settings.secrets
    if s.supabase_url and s.supabase_service_key:
        return SupabaseRepository(s.supabase_url, s.supabase_service_key)
    return SQLiteRepository(s.db_path)
