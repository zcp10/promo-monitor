import unittest
from unittest.mock import AsyncMock
from promo.config import load_settings
from promo.pipeline import Pipeline
from promo.storage import SQLiteRepository
from promo.parser import parse_post
from promo.title_filter import clean_title
from promo.browser_shopping import GoogleBrowserSource
from promo.pricing import _CACHE

class SearchRulesTests(unittest.IsolatedAsyncioTestCase):
    def pipe(self, config=None):
        _CACHE.clear()
        settings=load_settings()
        settings.raw['rules']['google_search']=config or {}
        settings.secrets.hasdata_api_key=None
        settings.secrets.reef_api_key=None
        settings.raw['pricing']['google_browser']['enabled']=True
        pipe=Pipeline(settings, SQLiteRepository(':memory:'),None)
        return pipe

    async def test_database_rules_update_browser_and_ignored_headlines(self):
        pipe=self.pipe()
        pipe.repo.remote_rules=AsyncMock(return_value=({'google_search':{'hourly_limit':10,'interval_seconds':60,
            'catalog_only':True,'allowed_categories':['fone'], 'ignored_headlines':['Só amanhã às sete']}}, []))
        await pipe.refresh_rules(force=True)
        self.assertEqual(pipe.browser_source.config['hourly_limit'],10)
        self.assertFalse(pipe._google_allowed(pipe.catalog.profile('Cafeteira Oster')))
        self.assertTrue(pipe._google_allowed(pipe.catalog.profile('Fone JBL Tune 770NC')))
        self.assertEqual(clean_title('Só amanhã às sete',pipe.title_rules),'')

    async def test_no_selected_categories_allows_all_unless_catalog_only(self):
        pipe=self.pipe({'allowed_categories':[]})
        self.assertTrue(pipe._google_allowed(pipe.catalog.profile('Camiseta Henley')))
        pipe.settings.raw['rules']['google_search']['catalog_only']=True
        self.assertFalse(pipe._google_allowed(pipe.catalog.profile('Camiseta Henley')))

    async def test_numeric_limits_are_bounded(self):
        pipe=self.pipe({'hourly_limit':1000,'interval_seconds':1})
        self.assertEqual(pipe.browser_source.config['hourly_limit'],60)
        self.assertEqual(pipe.browser_source.config['interval_seconds'],60)

    async def test_disallowed_product_does_not_open_google(self):
        pipe=self.pipe({'catalog_only':True,'allowed_categories':['fone','mouse']})
        pipe.sources=[];pipe.reserve=[];pipe.reef_source=None
        pipe.browser_source.search=AsyncMock(side_effect=AssertionError('Não deve pesquisar'))
        profile=pipe.catalog.profile('Camiseta Henley Masculina')
        _,status=await pipe._fetch_market_quotes('Camiseta Henley Masculina', profile,50)
        pipe.browser_source.search.assert_not_awaited()
        self.assertEqual(status,'sem_categoria_sem_busca_paga')

    async def test_selected_product_uses_google(self):
        pipe=self.pipe({'catalog_only':True,'allowed_categories':['fone']})
        pipe.sources=[];pipe.reserve=[];pipe.reef_source=None
        pipe.browser_source.search=AsyncMock(return_value=[])
        profile=pipe.catalog.profile('Fone JBL Tune 770NC')
        await pipe._fetch_market_quotes('Fone JBL Tune 770NC',profile,100)
        pipe.browser_source.search.assert_awaited_once()

class CouponTitleTests(unittest.TestCase):
    def test_scheduled_coupon_not_a_product(self):
        for title in ['RESGATE HOJE ÀS 19:00 USAR AMANHÃ','Resgate seu cupom hoje às 19 horas para usar amanhã']:
            self.assertIsNone(parse_post(title+'\nR$ 50,00\nhttps://example.com'))

    def test_coupon_instructions_do_not_hide_product(self):
        post='RESGATE HOJE ÀS 19:00 USAR AMANHÃ\nFone JBL Tune 770NC Azul\nR$ 99,90'
        self.assertEqual(parse_post(post).title,'Fone JBL Tune 770NC Azul')
        self.assertEqual(clean_title('Resgate Fone JBL Tune 770NC'),'Resgate Fone JBL Tune 770NC')

    def test_advertising_prefixes_preserve_real_product(self):
        self.assertEqual(clean_title('Olha o que eu encontrei! Mouse Logitech G203'),'Mouse Logitech G203')
        self.assertEqual(clean_title('Preção! Creatina Dark Lab 500g'),'Creatina Dark Lab 500g')
