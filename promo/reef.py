"""Última alternativa: Zoom/Buscapé pela API REST da ReefAPI."""
from __future__ import annotations

import hashlib
import time

import httpx

from .catalog import get_catalog
from .matcher import filter_quotes
from .models import Quote


class ReefSource:
    name = 'reef'
    BASE = 'https://api.reefapi.com/zoom-buscape/v1'

    def __init__(self, api_key, repo, site='buscape', limit=30, accept_used=False):
        self.api_key, self.repo = api_key, repo
        self.site, self.limit = site, limit
        self.accept_used = accept_used
        self.last_status = 'not_run'
        self.charged_credits = 0
        self._retry_at = 0

    async def _request(self, client, action, payload):
        response = await client.post(f'{self.BASE}/{action}',
            headers={'x-api-key': self.api_key}, json={**payload, 'site': self.site}, timeout=60)
        if response.status_code in (401, 403, 402, 429):
            self.last_status = {401: 'invalid_key', 403: 'access_denied',
                                402: 'quota_exhausted', 429: 'rate_limited'}[response.status_code]
            self._retry_at = time.monotonic() + (3600 if response.status_code == 429 else 86400)
            return None
        response.raise_for_status()
        body = response.json()
        self.charged_credits += int((body.get('meta') or {}).get('charged_credits') or 0)
        if body.get('ok') is not True:
            self.last_status = 'api_error'
            return None
        return body.get('data') or {}

    def _quote(self, row, title=''):
        seller = row.get('seller') or row.get('store') or {}
        if isinstance(seller, str):
            seller = {'name': seller}
        best = row.get('best_offer') or {}
        price = row.get('price')
        try:
            price = float(price)
        except (TypeError, ValueError):
            return None
        condition = str(row.get('condition') or '').casefold()
        condition = 'used' if condition in ('used', 'usado', 'usada', 'seminovo') else condition
        if price <= 0 or condition not in ('', 'new', 'novo', 'used'):
            return None
        if condition == 'used' and not self.accept_used:
            return None
        store = row.get('seller_name') or row.get('store_name') or seller.get('name') or best.get('seller_name')
        if not store:
            return None
        title = row.get('title') or row.get('offer_title') or title
        if condition == 'used':
            title += ' [usado]'
        return Quote(provider='reef', title=title, condition=condition or None,
                     price=price, store=store, url=row.get('clickout_url') or row.get('url'))

    async def search(self, client: httpx.AsyncClient, query: str) -> list[Quote]:
        if time.monotonic() < self._retry_at:
            return []
        self.charged_credits = 0
        key = 'reef-identity:v1:' + hashlib.sha256(' '.join(query.casefold().split()).encode()).hexdigest()
        identity = None
        try:
            hit = await self.repo.get_cache(key)
            if hit and hit.get('quotes'):
                identity = hit['quotes'][0]
        except Exception:
            pass  # Cache indisponível não impede consulta direta.
        if not identity:
            data = await self._request(client, 'search', {'query': query, 'page_size': 20})
            if data is None:
                return []
            catalog = get_catalog()
            profile = catalog.profile(query)
            candidates = []
            for row in data.get('results') or []:
                quote = Quote('reef', row.get('title') or '', 1)
                filter_quotes(query, [quote], exclude_words=['usado', 'recondicionado', 'capa'],
                              min_score=.6, profile=profile, catalog=catalog)
                if quote.accepted:
                    candidates.append((quote.match_score, row))
            # Prefere o produto de catálogo: permite obter várias lojas em uma chamada.
            candidates.sort(key=lambda x: (bool(x[1].get('product_id')),
                int(x[1].get('store_count') or 0), x[0]), reverse=True)
            if not candidates:
                self.last_status = 'no_compatible_product'
                return []
            row = candidates[0][1]
            if not row.get('product_id'):
                quote = self._quote(row)
                self.last_status = 'ok' if quote else 'no_offers'
                return [quote] if quote else []
            identity = {'product_id': row['product_id'], 'title': row.get('title') or query}
            try:
                await self.repo.put_cache(key, query, 'reef_identity', [identity])
            except Exception:
                pass
        data = await self._request(client, 'offers', {'product_id': identity['product_id'],
            'sort': 'price_asc', 'page_size': self.limit})
        if data is None:
            return []
        rows = data.get('offers') or data.get('results') or []
        # Guarda a resposta coletada inclusive quando o filtro não aceita a condição.
        try:
            await self.repo.put_cache('reef-offers:' + str(identity['product_id']), query, 'reef_raw_offers', rows)
        except Exception:
            pass
        quotes = [q for row in rows if (q := self._quote(row, identity['title']))]
        self.last_status = 'ok' if quotes else 'no_offers'
        return quotes
