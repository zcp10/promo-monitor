import asyncio
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
from promo.browser_shopping import GoogleBrowserSource
from promo.config import ROOT
from promo.scrapling_browser import search

spec = importlib.util.spec_from_file_location('pilot_policy', ROOT/'browser'/'pilot'/'policy.py')
policy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(policy)

class PilotPolicyTests(unittest.TestCase):
    def test_captcha_200_and_http_blocks(self):
        self.assertTrue(policy.blocked(200, 'https://www.google.com/sorry/index', ''))
        self.assertTrue(policy.blocked(200, '', 'Detectamos tráfego incomum'))
        self.assertTrue(policy.blocked(429, '', ''))
        self.assertFalse(policy.blocked(200, '', 'Fone JBL R$ 100,00'))

    def test_interval_hour_limit_no_daily_limit(self):
        self.assertEqual(policy.gate({'next_at': 120}, 100)['status'], 'interval')
        self.assertEqual(policy.gate({'attempts': [1,2,3,4,5]}, 100)['status'], 'hourly_limit')
        self.assertIsNone(policy.gate({'attempts': list(range(100))}, 4000))

    def test_progressive_pause_retry_after_reset(self):
        state = {}
        for delay in [60, 120, 300, 1800]:
            policy.finish(state, 100, failed=True)
            self.assertEqual(state['next_at'], 100+delay)
        policy.finish(state, 100, failed=True, retry_after=3600)
        self.assertEqual(state['next_at'], 3700)
        policy.finish(state, 100)
        self.assertEqual(state['failures'], 0)
        self.assertEqual(state['next_at'], 160)

class PilotTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_docker_absent_is_fast_empty_fallback(self):
        with patch('promo.scrapling_browser.asyncio.create_subprocess_exec', side_effect=FileNotFoundError):
            self.assertEqual((await search({}, 'JBL'))['status'], 'docker_unavailable')

    async def test_adapter_uses_docker_and_maps_quotes(self):
        response = {'status': 'ok', 'quotes': [{'provider': 'google_browser', 'title': 'JBL Tune 770NC', 'price': 400, 'store': 'Amazon'}]}
        with patch('promo.scrapling_browser.search', return_value=response) as rpc:
            source = GoogleBrowserSource({'backend': 'scrapling_docker'})
            quotes = await source.search(None, 'JBL Tune 770NC')
        self.assertEqual(quotes[0].price, 400)
        self.assertEqual(source.last_status, 'ok')
        rpc.assert_awaited_once()

    async def test_blocked_returns_no_quotes_and_status(self):
        with patch('promo.scrapling_browser.search', return_value={'status': 'blocked', 'quotes': []}):
            source = GoogleBrowserSource({'backend': 'scrapling_docker'})
            self.assertEqual(await source.search(None, 'JBL'), [])
            self.assertEqual(source.last_status, 'blocked')
