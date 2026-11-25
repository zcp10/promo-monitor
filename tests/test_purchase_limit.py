import unittest
from unittest.mock import AsyncMock

from promo.config import load_settings
from promo.models import Offer, Quote
from promo.pipeline import Pipeline
from promo.storage import SQLiteRepository


class PurchaseLimitTests(unittest.IsolatedAsyncioTestCase):
    def pipe(self):
        settings = load_settings()
        settings.raw['rules']['max_offer_price'] = 150
        settings.raw['pricing']['google_browser']['enabled'] = False
        pipe = Pipeline(settings, SQLiteRepository(':memory:'), None)
        pipe.refresh_rules = AsyncMock()
        pipe.repo.save_offer = AsyncMock(return_value=None)
        pipe._market_quotes_with_status = AsyncMock(side_effect=AssertionError('Não deve buscar'))
        return pipe

    async def test_expensive_offer_never_saved_or_searched(self):
        pipe = self.pipe()
        self.assertIsNone(await pipe.handle_offer(Offer('Monitor LG', 1000)))
        pipe.repo.save_offer.assert_not_awaited()
        pipe._market_quotes_with_status.assert_not_awaited()

    async def test_boundary_and_kit_use_promotion_unit_price(self):
        for price, quantity in [(150, 1), (300, 2), (88, 2)]:
            pipe = self.pipe()
            await pipe.handle_offer(Offer('Fone Havit', price, quantity=quantity))
            pipe.repo.save_offer.assert_awaited_once()
        pipe = self.pipe()
        await pipe.handle_offer(Offer('Fone Havit', 300.02, quantity=2))
        pipe.repo.save_offer.assert_not_awaited()

    async def test_changed_remote_limit_applies_before_filter(self):
        pipe = self.pipe()
        async def refresh():
            pipe.settings.raw['rules']['max_offer_price'] = 200
        pipe.refresh_rules = AsyncMock(side_effect=refresh)
        await pipe.handle_offer(Offer('Fone Havit', 180))
        pipe.repo.save_offer.assert_awaited_once()

    async def test_zero_disables_limit(self):
        pipe = self.pipe()
        pipe.settings.raw['rules']['max_offer_price'] = 0
        await pipe.handle_offer(Offer('Monitor LG', 1000))
        pipe.repo.save_offer.assert_awaited_once()

    async def test_direct_evaluation_skips_search(self):
        pipe = self.pipe()
        result = await pipe.evaluate(Offer('Monitor LG', 1000))
        self.assertTrue(result.profile['limite_preco'])
        pipe._market_quotes_with_status.assert_not_awaited()

    async def test_reference_price_not_limited(self):
        pipe = self.pipe()
        result = await pipe.evaluate(Offer('Fone JBL Tune 770NC', 100),
                                     [Quote('kabum', 'Fone JBL Tune 770NC', 500)])
        self.assertEqual(result.quotes[0].price, 500)
        self.assertNotIn('limite_preco', result.profile)
