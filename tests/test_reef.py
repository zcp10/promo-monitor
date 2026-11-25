import unittest
import httpx
from promo.reef import ReefSource
from promo.storage import SQLiteRepository


class ReefTests(unittest.IsolatedAsyncioTestCase):
    async def test_used_prices_are_labeled_and_accepted_when_enabled(self):
        from promo.matcher import filter_quotes
        from promo.storage import quote_rows
        source = ReefSource('test', SQLiteRepository(':memory:'), accept_used=True)
        quote = source._quote({'title':'JBL Tune 770NC','seller':{'name':'Want Import'},
                              'price':444.44,'condition':'used'})
        self.assertEqual(quote.condition, 'used')
        self.assertIn('[usado]', quote.title)
        filter_quotes('JBL Tune 770NC', [quote], exclude_words=['usado','defeito'],
                      min_score=.6, accept_used=True)
        self.assertTrue(quote.accepted)
        self.assertNotIn('condition',quote_rows([quote],'test')[0])
        quote.title += ' com defeito'
        filter_quotes('JBL Tune 770NC',[quote],exclude_words=['usado','defeito'],
                      min_score=.6,accept_used=True)
        self.assertFalse(quote.accepted)

    async def test_identity_is_saved_and_next_refresh_only_uses_offers(self):
        calls = []
        def respond(request):
            action = request.url.path.split('/')[-1]
            calls.append(action)
            data = {'results': [{'product_id':'123','title':'Fone JBL Tune 770NC','price':440}]} if action == 'search' else {
                'offers': [
                    {'title':'Fone JBL Tune 770NC','price':440,'seller':{'name':'Amazon'},'condition':'new'},
                    {'title':'Fone JBL Tune 770NC','price':200,'seller':{'name':'Usados'},'condition':'used'}]}
            return httpx.Response(200, json={'ok':True,'data':data,'meta':{'charged_credits':2 if action=='search' else 1}})
        source = ReefSource('test-not-real', SQLiteRepository(':memory:'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            quotes = await source.search(client,'JBL Tune 770NC')
            self.assertEqual(len(quotes),1)
            self.assertEqual(source.charged_credits,3)
            await source.search(client,'JBL Tune 770NC')
            self.assertEqual(source.charged_credits,1)
        self.assertEqual(calls,['search','offers','offers'])

    async def test_wrong_model_never_triggers_offers_call(self):
        def respond(request):
            self.assertTrue(request.url.path.endswith('/search'))
            return httpx.Response(200,json={'ok':True,'data':{'results':[{'product_id':'x','title':'JBL Live 770NC'}]}})
        source = ReefSource('test',SQLiteRepository(':memory:'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(await source.search(client,'JBL Tune 770NC'),[])

    async def test_exhausted_quota_is_not_retried_immediately(self):
        calls = []
        def respond(request):
            calls.append(1)
            return httpx.Response(402,json={'ok':False})
        source = ReefSource('test',SQLiteRepository(':memory:'))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            self.assertEqual(await source.search(client,'JBL Tune 770NC'),[])
            self.assertEqual(await source.search(client,'Outro produto'),[])
        self.assertEqual(calls,[1])
        self.assertEqual(source.last_status,'quota_exhausted')
