"""Fontes de preço de mercado.

- Google Shopping (via HasData): junta Mercado Livre, Magalu, KaBuM!, Amazon,
  Casas Bahia etc. num só resultado. É a fonte principal.
- Mercado Livre (API oficial): complementa com anúncios novos do ML.
- Página específica (via HasData scrape com extração por IA): para a watchlist,
  quando você quer acompanhar uma URL exata da Kabum/Magalu.

Todas devolvem `list[Quote]`; quem decide se é o mesmo produto é o matcher.
"""
from __future__ import annotations

import logging
import re
from typing import Protocol

import httpx

from .config import Settings
from .matcher import STOPWORDS, capacities, model_codes, tokens
from .models import Quote
from .parser import parse_brl
from .title_filter import clean_title

log = logging.getLogger(__name__)
HASDATA_BASE = "https://api.hasdata.com"
MELI_BASE = "https://api.mercadolibre.com"


def _to_price(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if value > 0 else None
    if isinstance(value, dict):
        return _to_price(value.get("value") or value.get("extractedValue"))
    text = str(value)
    match = re.search(r"[\d.]+(?:,\d{1,2})?", text.replace("R$", ""))
    return parse_brl(match.group(0)) if match else None


def build_query(title: str, max_words: int = 9) -> str:
    """Encurta o título para uma busca boa: mantém marca, modelo e capacidade."""
    title = clean_title(title)
    words = re.findall(r"[\wÀ-ÿ\-\"]+", title)
    toks = tokens(title)
    important = model_codes(toks) | {c.lower() for c in capacities(title)}
    kept, seen = [], set()
    for w in words:
        lw = w.lower()
        if lw in seen or lw in STOPWORDS:  # "de", "com"... não podem empurrar o modelo para fora
            continue
        if lw in important or len(kept) < max_words:
            kept.append(w)
            seen.add(lw)
    return " ".join(kept)


class PriceSource(Protocol):
    name: str

    async def search(self, client: httpx.AsyncClient, query: str) -> list[Quote]: ...


class GoogleShoppingSource:
    name = "google_shopping"

    def __init__(self, api_key: str, country: str = "br", language: str = "pt-br", limit: int = 20):
        self.api_key, self.country, self.language, self.limit = api_key, country, language, limit
        self.last_status = "not_run"
        self._retry_at = 0

    async def search(self, client: httpx.AsyncClient, query: str) -> list[Quote]:
        import time
        if time.monotonic() < self._retry_at:
            return []
        resp = await client.get(
            f"{HASDATA_BASE}/scrape/google/shopping",
            params={"q": query, "gl": self.country, "hl": self.language},
            headers={"x-api-key": self.api_key},
            timeout=60,
        )
        if resp.status_code in (401, 402, 403, 429):
            self.last_status = "quota_or_access_" + str(resp.status_code)
            self._retry_at = time.monotonic() + 3600
            return []
        resp.raise_for_status()
        data = resp.json()
        quotes = []
        for item in (data.get("shoppingResults") or [])[: self.limit]:
            price = _to_price(item.get("extractedPrice") or item.get("price"))
            if not price:
                continue
            delivery = str(item.get("delivery") or "").lower()
            if "grátis" in delivery or "gratis" in delivery or "free" in delivery:
                shipping = 0.0
            elif "r$" in delivery:  # só lê valor quando há preço explícito ("Entrega em 3 dias" não é R$ 3)
                shipping = _to_price(delivery)
            else:
                shipping = None
            quotes.append(Quote(
                provider=self.name,
                title=item.get("title") or "",
                price=price,
                store=item.get("source") or item.get("merchant"),
                shipping=shipping,
                url=item.get("link") or item.get("productLink"),
            ))
        self.last_status = "ok" if quotes else "no_results"
        return quotes


class MercadoLivreSource:
    name = "mercadolivre"

    def __init__(self, access_token: str | None, limit: int = 20):
        self.token, self.limit = access_token, limit
        self.refused = False  # após um 401/403, não tenta de novo até reiniciar

    async def search(self, client: httpx.AsyncClient, query: str) -> list[Quote]:
        if self.refused:
            return []
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        resp = await client.get(
            f"{MELI_BASE}/sites/MLB/search",
            params={"q": query, "limit": self.limit, "condition": "new"},
            headers=headers, timeout=30,
        )
        if resp.status_code in (401, 403):
            log.warning("Mercado Livre recusou a busca (%s); fonte desligada até reiniciar. "
                        "Configure MELI_ACCESS_TOKEN.", resp.status_code)
            self.refused = True
            return []
        resp.raise_for_status()
        quotes = []
        for item in resp.json().get("results", []):
            if item.get("condition") not in (None, "new"):
                continue
            free = (item.get("shipping") or {}).get("free_shipping")
            quotes.append(Quote(
                provider=self.name, title=item.get("title", ""), price=float(item.get("price") or 0),
                store="Mercado Livre", shipping=0.0 if free else None, url=item.get("permalink"),
            ))
        return [q for q in quotes if q.price > 0]


class KabumSource:
    """Busca pública da KaBuM! (grátis).

    Usa só /busca/<termo> (sem "?"), que o robots.txt da KaBuM permite, no máximo
    1 requisição a cada `interval` segundos. Os dados vêm do JSON que a própria
    página entrega (__NEXT_DATA__). Ignora open box e itens indisponíveis.
    """
    name = "kabum"
    BASE = "https://www.kabum.com.br"
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
        "Accept-Language": "pt-BR,pt;q=0.9",
    }

    def __init__(self, limit: int = 30, interval: float = 3.0):
        import asyncio

        self.limit, self.interval = limit, interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    @staticmethod
    def slug(query: str) -> str:
        from .matcher import normalize

        words = re.findall(r"[a-z0-9]+", normalize(query).replace(",", "."))
        return "-".join(words)

    async def search(self, client: httpx.AsyncClient, query: str) -> list[Quote]:
        import asyncio
        import json
        import time

        slug = self.slug(query)
        if not slug:
            return []
        async with self._lock:  # ritmo educado: uma página por vez
            wait = self.interval - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            resp = await client.get(f"{self.BASE}/busca/{slug}", headers=self.HEADERS, timeout=30)
            self._last = time.monotonic()
        resp.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
        if not m:
            log.warning("KaBuM: página sem __NEXT_DATA__ (layout mudou?)")
            return []
        try:
            data = json.loads(m.group(1))["props"]["pageProps"]["data"]
        except (KeyError, TypeError, ValueError):
            log.warning("KaBuM: estrutura inesperada (layout mudou?)")
            return []
        catalog = data.get("catalogServer") if isinstance(data, dict) else None
        if not catalog:
            log.debug("KaBuM: nenhum resultado para '%s'", query)  # página "não encontramos"
            return []
        items = catalog.get("data") or []
        quotes = []
        for it in items[: self.limit]:
            flags = it.get("flags") or {}
            if not it.get("available") or flags.get("isOpenbox"):
                continue
            offer = it.get("offer") or {}
            price = _to_price(offer.get("priceWithDiscount") or it.get("priceWithDiscount") or it.get("price"))
            if not price:
                continue
            quotes.append(Quote(
                provider=self.name,
                title=it.get("name") or "",
                price=price,
                store="KaBuM!" if not flags.get("isMarketplace") else f"KaBuM! ({it.get('sellerName')})",
                shipping=0.0 if flags.get("isFreeShipping") else None,
                url=f"{self.BASE}/produto/{it.get('code')}/{it.get('friendlyName', '')}",
            ))
        return quotes


class PageScraper:
    """Lê o preço atual de uma URL específica (watchlist) via HasData + extração por IA."""

    def __init__(self, api_key: str):
        self.api_key = api_key

    async def current_price(self, client: httpx.AsyncClient, url: str) -> Quote | None:
        resp = await client.post(
            f"{HASDATA_BASE}/scrape/web",
            json={
                "url": url,
                "jsRendering": True,
                "aiExtractRules": {
                    "title": {"type": "string", "description": "nome do produto"},
                    "price": {"type": "number", "description": "preço à vista atual em reais"},
                    "available": {"type": "boolean", "description": "produto disponível para compra"},
                },
            },
            headers={"x-api-key": self.api_key},
            timeout=90,
        )
        resp.raise_for_status()
        data = resp.json().get("aiResponse") or {}
        price = _to_price(data.get("price"))
        if not price or data.get("available") is False:
            return None
        return Quote(provider="scrape", title=data.get("title") or url, price=price, url=url)


def build_sources(settings: Settings) -> tuple[list[PriceSource], list[PriceSource]]:
    """(principais, reserva). Reserva só é consultada se as principais trouxerem pouco."""
    s, main, reserve = settings.secrets, [], []
    kb = settings.get("pricing.kabum", {}) or {}
    if kb.get("enabled", True):
        main.append(KabumSource(int(kb.get("max_results", 30)), float(kb.get("interval_seconds", 3))))
    ml = settings.get("pricing.mercadolivre", {}) or {}
    if ml.get("enabled", True):
        main.append(MercadoLivreSource(s.meli_access_token, int(ml.get("max_results", 20))))
    gs = settings.get("pricing.google_shopping", {}) or {}
    if gs.get("enabled", True) and s.hasdata_api_key:
        src = GoogleShoppingSource(s.hasdata_api_key, gs.get("country", "br"),
                                   gs.get("language", "pt-br"), int(gs.get("max_results", 20)))
        reserve.append(src)  # Sempre depois do Chrome; nunca em paralelo com ele.
    return main, reserve


_CACHE: dict[tuple[str, str], tuple[float, list[Quote]]] = {}
CACHE_TTL = 24 * 3600


async def collect_quotes(sources: list[PriceSource], query: str) -> list[Quote]:
    """Consulta as fontes em paralelo. Resultado fica em cache por 24 h por fonte+busca
    (a mesma promoção repostada em vários grupos não gasta crédito de novo)."""
    import asyncio
    import copy
    import time

    now = time.monotonic()
    cached: list[Quote] = []
    todo = []
    for src in sources:
        hit = _CACHE.get((src.name, query.lower()))
        if hit and now - hit[0] < CACHE_TTL:
            cached.extend(copy.deepcopy(hit[1]))
            if hasattr(src, "last_status"):
                src.last_status = "cached"
        else:
            todo.append(src)
    async def search_with_retry(client, src):
        # queda rápida de internet/DNS não pode virar "sem preço" para sempre
        for attempt in range(3):
            try:
                return await src.search(client, query)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                if attempt == 2:
                    raise
                log.info("Fonte %s: erro de rede (%s); tentando de novo em %ss.", src.name, exc, 5 * (attempt + 1))
                await asyncio.sleep(5 * (attempt + 1))

    async with httpx.AsyncClient(follow_redirects=True) as client:
        results = await asyncio.gather(*(search_with_retry(client, src) for src in todo),
                                       return_exceptions=True)
    quotes: list[Quote] = cached
    for src, res in zip(todo, results):
        if isinstance(res, Exception):
            log.warning("Fonte %s falhou: %s", src.name, res)
            continue
        if res:  # Falha, bloqueio ou busca vazia não podem ficar em cache por 24 h.
            _CACHE[(src.name, query.lower())] = (now, copy.deepcopy(res))
        quotes.extend(res)
    # remove duplicados (mesma loja, mesmo preço, mesmo título)
    seen, unique = set(), []
    for q in quotes:
        key = ((q.store or "").lower(), round(q.price, 2), q.title.lower()[:60])
        if key not in seen:
            seen.add(key)
            unique.append(q)
    return unique
