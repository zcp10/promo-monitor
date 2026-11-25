import unittest
from unittest.mock import AsyncMock
from promo.config import load_settings
from promo.models import Offer, Quote
from promo.pipeline import Pipeline
from promo.storage import SQLiteRepository
from promo.pricing import _CACHE

TITLE = 'Fone JBL Tune 770NC'


class Source:
    def __init__(self, name, events, good=False, wrong=False, fail=False):
        self.name, self.events, self.good, self.wrong, self.fail = name, events, good, wrong, fail
        self.last_status = 'ok' if good else 'quota_exhausted'

    async def search(self, client, query):
        self.events.append(self.name)
        if self.fail:
            raise RuntimeError('Falha simulada')
        return [Quote(self.name, TITLE if not self.wrong else 'Capa JBL Tune 770NC', 440, store='Amazon')] if self.good else []


class SequentialTests(unittest.IsolatedAsyncioTestCase):
    def pipe(self, winning=None):
        _CACHE.clear()
        settings = load_settings()
        settings.raw["rules"]["google_search"] = {}  # Testa fontes sem filtro de categorias da produção.
        settings.raw['rules']['max_offer_price'] = 150
        settings.raw['rules']['segunda_opiniao_limite_diario'] = 10
        pipe = Pipeline(settings, SQLiteRepository(':memory:'), None)
        events = []
        pipe.sources = [Source('kabum', events, winning == 'kabum')]
        pipe.browser_source = Source('google_browser', events, winning == 'google_browser')
        pipe.reserve = [Source('google_shopping', events, winning == 'google_shopping')]
        pipe.reef_source = Source('reef', events, winning == 'reef')
        return pipe, events

    async def test_stops_at_first_compatible_source(self):
        order = ['kabum', 'google_browser', 'google_shopping', 'reef']
        for winner in order:
            pipe, events = self.pipe(winner)
            result = await pipe.evaluate(Offer(TITLE, 100))
            self.assertEqual(events, order[:order.index(winner)+1])
            self.assertEqual(len(result.accepted_quotes), 1)

    async def test_hasdata_quota_skips_to_reef(self):
        pipe, events = self.pipe('reef')
        pipe.settings.raw['rules']['segunda_opiniao_limite_diario'] = 0
        await pipe.evaluate(Offer(TITLE, 100))
        self.assertEqual(events, ['kabum', 'google_browser', 'reef'])

    async def test_wrong_chrome_result_does_not_stop_fallback(self):
        pipe, events = self.pipe('google_shopping')
        pipe.browser_source.good = pipe.browser_source.wrong = True
        await pipe.evaluate(Offer(TITLE, 100))
        self.assertEqual(events, ['kabum', 'google_browser', 'google_shopping'])

    async def test_hasdata_error_continues_to_reef(self):
        pipe, events = self.pipe('reef')
        pipe.reserve[0].fail = True
        await pipe.evaluate(Offer(TITLE, 100))
        self.assertEqual(events, ['kabum', 'google_browser', 'google_shopping', 'reef'])

    async def test_monthly_reference_skips_every_source(self):
        pipe, events = self.pipe('reef')
        await pipe.evaluate(Offer(TITLE, 100))
        events.clear()
        await pipe.evaluate(Offer(TITLE, 90))
        self.assertEqual(events, [])

    async def test_purchase_cap_skips_every_source(self):
        pipe, events = self.pipe('reef')
        await pipe.evaluate(Offer(TITLE, 151))
        self.assertEqual(events, [])

    async def test_no_result_is_remembered_and_not_paid_again(self):
        pipe, events = self.pipe(None)          # ninguém acha nada
        await pipe.evaluate(Offer(TITLE, 100))
        self.assertEqual(events, ['kabum', 'google_browser', 'google_shopping', 'reef'])
        events.clear()
        _CACHE.clear()
        a = await pipe.evaluate(Offer(TITLE, 95))  # mesma busca repostada: paga nada
        self.assertEqual(events, ['kabum', 'google_browser'])
        self.assertEqual(a.profile.get('google_browser'), 'sem_resultado_recente')

    async def test_uncategorized_product_never_uses_paid_sources(self):
        pipe, events = self.pipe(None)
        await pipe.evaluate(Offer('Bermudas Masculinas Academia Treino Praia Verão Fit', 60))
        self.assertEqual(events, ['kabum', 'google_browser'])

    async def test_reef_daily_limit_and_credit_log(self):
        pipe, events = self.pipe('reef')
        pipe.reef_source.charged_credits = 3
        pipe.settings.raw['rules']['segunda_opiniao_limite_diario'] = 0
        pipe.settings.raw['rules']['reef_limite_diario'] = 1
        await pipe.evaluate(Offer(TITLE, 100))
        self.assertIn('reef', events)
        events.clear()
        await pipe.evaluate(Offer('Fone JBL Tune 520BT', 100))  # outro produto, limite 1/dia já usado
        self.assertNotIn('reef', events)
        used = [c for c in pipe.repo._cache.values() if c['provider'] == 'reef_usage']
        self.assertEqual(used[0]['quotes'][0]['credits'], 3)
