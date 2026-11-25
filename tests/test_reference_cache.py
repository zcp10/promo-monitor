import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from promo.config import load_settings
from promo.models import Offer, Quote
from promo.pipeline import Pipeline
from promo.storage import SQLiteRepository


TITLE = 'Fone JBL Tune 770NC'


class ReferenceCacheTests(unittest.IsolatedAsyncioTestCase):
    def pipe(self):
        settings = load_settings()
        settings.raw['pricing']['reference_cache_days'] = 30
        settings.raw['pricing']['google_browser']['enabled'] = False
        pipe = Pipeline(settings, SQLiteRepository(':memory:'), None)
        pipe._fetch_market_quotes = AsyncMock(return_value=(
            [Quote('kabum', TITLE, 440, store='KaBuM!')], None))
        return pipe

    async def test_new_promotion_reuses_reference_and_keeps_original_date(self):
        pipe = self.pipe()
        await pipe.evaluate(Offer(TITLE, 100))
        key = next(iter(pipe.repo._cache))
        when = (datetime.now(timezone.utc) - timedelta(days=29)).isoformat()
        pipe.repo._cache[key]['created_at'] = when
        result = await pipe.evaluate(Offer(TITLE, 90))
        self.assertEqual(result.offer.price, 90)
        self.assertEqual(result.market_median, 440)
        self.assertEqual(result.profile['referencia_mercado_coletada_em'], when)
        pipe._fetch_market_quotes.assert_awaited_once()
        self.assertEqual(pipe.repo._cache[key]['created_at'], when)

    async def test_expired_reference_is_refreshed_on_next_offer(self):
        pipe = self.pipe()
        await pipe.evaluate(Offer(TITLE, 100))
        key = next(iter(pipe.repo._cache))
        pipe.repo._cache[key]['created_at'] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        pipe._fetch_market_quotes.return_value = ([Quote('kabum', TITLE, 420, store='KaBuM!')], None)
        result = await pipe.evaluate(Offer(TITLE, 100))
        self.assertEqual(result.market_median, 420)
        self.assertEqual(pipe._fetch_market_quotes.await_count, 2)

    async def test_wrong_model_is_not_reused(self):
        pipe = self.pipe()
        await pipe.evaluate(Offer(TITLE, 100))
        key = next(iter(pipe.repo._cache))
        pipe.repo._cache[key]['quotes'][0]['title'] = 'Fone JBL Tune 670NC'
        await pipe.evaluate(Offer(TITLE, 100))
        self.assertEqual(pipe._fetch_market_quotes.await_count, 2)

    async def test_failures_and_empty_results_do_not_create_reference(self):
        pipe = self.pipe()
        pipe._fetch_market_quotes.return_value = ([], 'blocked')
        await pipe.evaluate(Offer(TITLE, 100))
        await pipe.evaluate(Offer(TITLE, 90))
        self.assertEqual(pipe._fetch_market_quotes.await_count, 2)
        self.assertFalse(getattr(pipe.repo, '_cache', {}))

    async def test_cache_failure_falls_back_to_sources(self):
        pipe = self.pipe()
        pipe.repo.get_cache = AsyncMock(side_effect=RuntimeError('Falha de teste'))
        result = await pipe.evaluate(Offer(TITLE, 100))
        self.assertEqual(result.market_median, 440)

    async def test_browser_cached_data_does_not_restart_validity(self):
        pipe = self.pipe()
        pipe._fetch_market_quotes.return_value = ([Quote('google_browser', TITLE, 440, store='Amazon')], 'cached')
        await pipe.evaluate(Offer(TITLE, 100))
        self.assertFalse(getattr(pipe.repo, '_cache', {}))
