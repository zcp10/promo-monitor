"""Testes locais: integração sem Google, Telegram ou banco de produção."""
import asyncio
import unittest
from promo.catalog import get_catalog
from promo.config import load_settings as _load_settings
from promo.matcher import filter_quotes
from promo.models import Offer, Quote
from promo.pipeline import Pipeline
from promo.pricing import collect_quotes, _CACHE
from promo.storage import SQLiteRepository


def load_settings():
    settings = _load_settings()
    settings.raw["rules"]["google_search"] = {}  # Testa fontes sem filtro de categorias da produção.
    settings.secrets.hasdata_api_key = None
    settings.secrets.reef_api_key = None  # A suíte nunca usa créditos reais.
    settings.raw["pricing"]["reef"]["enabled"] = False
    settings.raw["rules"]["max_offer_price"] = 0  # Testa fallback independentemente do orçamento.
    return settings


class FakeSource:
    def __init__(self, name, quotes=None, failure=False):
        self.name, self.quotes, self.failure = name, quotes or [], failure
        self.calls = 0
        self.last_status = "ok"

    async def search(self, client, query):
        self.calls += 1
        if self.failure:
            raise RuntimeError("Falha de teste na KaBuM")
        return self.quotes


class BrowserShoppingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _CACHE.clear()

    async def test_no_data_is_not_cached_for_a_day(self):
        class Source:
            name = "empty_test"
            calls = 0
            async def search(self, client, query):
                self.calls += 1
                return []
        source = Source()
        await collect_quotes([source], "JBL Tune 770NC")
        await collect_quotes([source], "JBL Tune 770NC")
        self.assertEqual(source.calls, 2)

    async def test_one_compatible_kabum_quote_skips_google(self):
        class Free:
            name = "free_test"
            async def search(self, client, query):
                return [Quote("kabum", "Fone JBL Tune 770NC Preto", 440, store="KaBuM!")]
        class Browser:
            name = "google_browser_test"
            last_status = "ok"
            calls = 0
            async def search(self, client, query):
                self.calls += 1
                return [Quote("google_browser", "Fone JBL Tune 770NC Preto", p, store=s)
                        for p,s in [(450,"Amazon"),(460,"Magalu")]]
        class Silent:
            async def send(self, text):
                raise AssertionError("Teste não pode enviar Telegram")
        pipe=Pipeline(load_settings(), SQLiteRepository(":memory:"), Silent())
        pipe.sources,pipe.reserve=[Free()],[]
        pipe.browser_source=Browser()
        a=await pipe.evaluate(Offer(title="Fone JBL Tune 770NC",price=250,shipping_cost=0))
        self.assertEqual(pipe.browser_source.calls,0)
        self.assertEqual(len(a.accepted_quotes),1)
        self.assertEqual(a.profile["google_browser"],"dispensado_kabum")

    async def test_block_does_not_create_quotes(self):
        class Blocked:
            name="blocked_test"
            last_status="blocked"
            async def search(self,client,query): return []
        pipe=Pipeline(load_settings(),SQLiteRepository(":memory:"),None)
        pipe.sources,pipe.reserve=[],[]
        pipe.browser_source=Blocked()
        a=await pipe.evaluate(Offer(title="Fone JBL Tune 770NC",price=101))
        self.assertEqual(a.accepted_quotes,[])
        self.assertIsNone(a.market_median)
        self.assertFalse(a.is_profitable)
        self.assertEqual(a.profile["google_browser"],"blocked")

    async def test_wrong_jbl_family_and_accessory_rejected(self):
        catalog=get_catalog()
        title="Fone JBL Tune 770NC"
        qs=[Quote("google_browser",t,450,store="Amazon") for t in
            ["Fone JBL Tune 770NC Preto","Fone JBL Live 770NC Preto","Capa para JBL Tune 770NC"]]
        filter_quotes(title,qs,exclude_words=["capa"],min_score=.6,
                      profile=catalog.profile(title),catalog=catalog)
        self.assertEqual([q.accepted for q in qs],[True,False,False])

    async def test_browser_can_be_disabled(self):
        settings=load_settings()
        settings.raw["pricing"]["google_browser"]["enabled"]=False
        pipe=Pipeline(settings,SQLiteRepository(":memory:"),None)
        self.assertIsNone(pipe.browser_source)

    def make_pipeline(self, free):
        pipe=Pipeline(load_settings(),SQLiteRepository(":memory:"),None)
        pipe.sources=[free]
        pipe.reserve=[FakeSource("google_shopping")]
        pipe.browser_source=FakeSource("google_browser",[
            Quote("google_browser","Fone JBL Tune 770NC Preto",price,store=store)
            for price,store in [(440,"Amazon"),(450,"Magalu"),(460,"Mercado Livre")]])
        return pipe

    async def test_empty_kabum_uses_google_and_not_paid_source(self):
        pipe=self.make_pipeline(FakeSource("kabum"))
        a=await pipe.evaluate(Offer(title="Fone JBL Tune 770NC",price=250))
        self.assertEqual(pipe.browser_source.calls,1)
        self.assertEqual(len(a.accepted_quotes),3)
        self.assertEqual(pipe.reserve[0].calls,0)

    async def test_unrelated_kabum_results_still_use_google(self):
        free=FakeSource("kabum",[Quote("kabum","Fone JBL Tune 780NC",450,store="KaBuM!")])
        pipe=self.make_pipeline(free)
        a=await pipe.evaluate(Offer(title="Fone JBL Tune 770NC",price=250))
        self.assertEqual(pipe.browser_source.calls,1)
        self.assertFalse(a.quotes[0].accepted)

    async def test_profitable_kabum_skips_both_google_sources(self):
        free=FakeSource("kabum",[Quote("kabum","Fone JBL Tune 770NC",p,store="KaBuM!")
                                 for p in (440,450,460)])
        pipe=self.make_pipeline(free)
        a=await pipe.evaluate(Offer(title="Fone JBL Tune 770NC",price=250,shipping_cost=0))
        self.assertTrue(a.is_profitable)
        self.assertEqual(pipe.browser_source.calls,0)
        self.assertEqual(pipe.reserve[0].calls,0)

    async def test_kabum_failure_uses_google(self):
        pipe=self.make_pipeline(FakeSource("kabum",failure=True))
        a=await pipe.evaluate(Offer(title="Fone JBL Tune 770NC",price=250))
        self.assertEqual(pipe.browser_source.calls,1)
        self.assertEqual(len(a.accepted_quotes),3)

    async def test_watchlist_market_query_has_same_fallback(self):
        pipe=self.make_pipeline(FakeSource("kabum"))
        qs=await pipe.market_quotes("Fone JBL Tune 770NC",pipe.catalog.profile("Fone JBL Tune 770NC"))
        self.assertEqual(pipe.browser_source.calls,1)
        self.assertEqual(sum(q.accepted for q in qs),3)

    async def test_uncatalogued_product_can_use_browser(self):
        pipe=self.make_pipeline(FakeSource("kabum"))
        title="Lanterna Acme XZ900"
        self.assertIsNone(pipe.catalog.profile(title).category)
        pipe.browser_source.quotes=[Quote("google_browser",title,150,store="Amazon")]
        a=await pipe.evaluate(Offer(title=title,price=80))
        self.assertEqual(pipe.browser_source.calls,1)
        self.assertEqual(len(a.accepted_quotes),1)

    async def test_active_browser_keeps_hasdata_only_as_reserve(self):
        from promo.pricing import build_sources
        settings=load_settings()
        settings.raw["pricing"]["google_shopping"]["enabled"]=True
        settings.raw["pricing"]["google_shopping"]["mode"]="principal"
        settings.secrets.hasdata_api_key="test-key-not-used"
        main,reserve=build_sources(settings)
        self.assertNotIn("google_shopping",[s.name for s in main])
        self.assertIn("google_shopping",[s.name for s in reserve])


if __name__=="__main__": unittest.main()
