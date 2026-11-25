"""Adaptador opcional do coletor Playwright; não depende de APIs pagas."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from .config import ROOT
from .models import Quote

log = logging.getLogger(__name__)


class GoogleBrowserSource:
    name = "google_browser"

    def __init__(self, config: dict):
        self.config = config
        self.last_status = "not_run"
        self._lock = asyncio.Lock()

    async def search(self, client, query: str) -> list[Quote]:
        async with self._lock:
            cfg = self.config
            if cfg.get("backend") == "scrapling_docker":
                from .scrapling_browser import search
                result = await search(cfg, query)
                self.last_status = result.get("status", "error")
                log.info("Google Scrapling: %s | cotações: %s | busca: %s",
                         self.last_status, len(result.get("quotes", [])), query)
                if self.last_status not in ("ok", "cached"):
                    return []
                return [Quote(**q) for q in result.get("quotes", [])]
            options = {
                "stateDir": str(ROOT / ".shopping-browser"),
                "intervalSeconds": float(cfg.get("interval_seconds", 60)),
                "hourlyLimit": int(cfg.get("hourly_limit", 5)),
                "cooldownHours": float(cfg.get("cooldown_hours", 24)),
                "cacheHours": float(cfg.get("cache_hours", 720)),
                "headless": bool(cfg.get("headless", False)),
                "channel": cfg.get("channel", "chrome"),
                "maxResults": int(cfg.get("max_results", 30)),
            }
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    cfg.get("node_executable", "node"), str(ROOT / "browser" / "google-shopping.cjs"),
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(
                    json.dumps({"query": query, "options": options}).encode("utf-8")), timeout=300)
                if proc.returncode:
                    raise RuntimeError(stderr.decode("utf-8", errors="replace")[:300])
                result = json.loads(stdout.decode("utf-8"))
                self.last_status = result.get("status", "error")
                if self.last_status not in ("ok", "cached"):
                    log.warning("Google navegador: %s (sem cotações)", self.last_status)
                    return []
                return [Quote(**q) for q in result.get("quotes", [])]
            except Exception as exc:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                self.last_status = "error"
                log.warning("Google navegador indisponível: %s", exc)
                return []
