"""Testes da lógica principal (rodam sem internet): python -m unittest discover tests"""
import unittest

from promo.config import load_settings as _load_settings
from promo.matcher import filter_quotes, similarity
from promo.models import Offer, Quote
from promo.parser import parse_brl, parse_post
from promo.profit import analyze, breakeven_price


def load_settings():
    """A suíte legada testa a lógica sem abrir o navegador de produção."""
    settings = _load_settings()
    settings.secrets.hasdata_api_key = None
    settings.secrets.reef_api_key = None  # A suíte nunca usa créditos reais.
    settings.raw["pricing"]["reef"]["enabled"] = False
    settings.raw["rules"]["max_offer_price"] = 0  # Estes testes isolam a comparação de mercado.
    settings.raw.setdefault("pricing", {}).setdefault("google_browser", {})["enabled"] = False
    return settings

POST_ML = """🔥 OFERTA RELÂMPAGO 🔥

Smartphone Samsung Galaxy A55 5G 256GB 8GB RAM Azul

De R$ 2.499,00 por R$ 1.599,00
💳 ou 10x de R$ 159,90
🚚 Frete grátis

🛒 https://mercadolivre.com/sec/1AbCdEf
"""

POST_SHOPEE = """⚡ Fone Bluetooth JBL Tune 520BT Preto
~~R$ 349,90~~
💰 Sai por R$ 189,90 com cupom
🎟️ Cupom: JBL40OFF
👉 https://s.shopee.com.br/abc123
"""

POST_ALI = """Achadinho!! SSD Kingston NV2 1TB NVMe M.2
R$ 289
use o código ALIBR30
https://s.click.aliexpress.com/e/_xyz
"""

POST_NOT_OFFER = "Bom dia pessoal! Hoje tem live às 20h no canal 😉"


class ParserTests(unittest.TestCase):
    def test_brl(self):
        self.assertEqual(parse_brl("1.799,90"), 1799.90)
        self.assertEqual(parse_brl("1.799"), 1799.0)
        self.assertEqual(parse_brl("99,9"), 99.9)
        self.assertEqual(parse_brl("289"), 289.0)

    def test_mercadolivre_post(self):
        o = parse_post(POST_ML)
        self.assertIsNotNone(o)
        self.assertIn("Galaxy A55", o.title)
        self.assertEqual(o.price, 1599.0)
        self.assertEqual(o.old_price, 2499.0)
        self.assertEqual(o.store, "Mercado Livre")
        self.assertEqual(o.shipping_cost, 0.0)

    def test_shopee_post_with_coupon(self):
        o = parse_post(POST_SHOPEE)
        self.assertEqual(o.price, 189.90)
        self.assertEqual(o.old_price, 349.90)
        self.assertEqual(o.coupon, "JBL40OFF")
        self.assertEqual(o.store, "Shopee")
        self.assertIn("JBL Tune 520BT", o.title)

    def test_single_line_post(self):
        o = parse_post("Smartphone Samsung Galaxy A55 5G 256GB por R$ 1.599,00 frete grátis https://mercadolivre.com/x")
        self.assertIsNotNone(o)
        self.assertEqual(o.title, "Smartphone Samsung Galaxy A55 5G 256GB")
        self.assertEqual(o.price, 1599.0)
        self.assertEqual(o.shipping_cost, 0.0)
        self.assertEqual(o.store, "Mercado Livre")

    def test_long_post_title_matches_short_listing(self):
        offer = "Placa-Mãe ASUS TUF GAMING A520M-PLUS II, AMD AM4, mATX, DDR4, 90MB1F00-M0EAY0"
        self.assertGreaterEqual(similarity(offer, "Asus Placa-Mãe TUF Gaming A520M-PLUS II"), 0.9)
        cpu = "Processador AMD Ryzen 7 9800X3D, 4.7GHz (5.2GHz Max Turbo), Cache 104MB, AM5"
        self.assertGreaterEqual(similarity(cpu, "Processador AMD Ryzen 7 9800X3D"), 0.9)

    def test_model_suffix_and_size_mismatch(self):
        self.assertEqual(similarity("Placa-Mãe ASRock B650 Pro RS, AM5, ATX", "ASRock Placa-Mãe B650M Pro RS WIFI"), 0.0)
        self.assertEqual(similarity('Bettdow Monitor De Tela Dupla De 15.4" Cada', "Bettdow Monitor Portatil 15,6'', FHD"), 0.0)

    def test_exclude_stores(self):
        quotes = [Quote(provider="google_shopping", store="OLX", title="Samsung Galaxy A55 5G 256GB", price=1400.0),
                  Quote(provider="google_shopping", store="Magazine Luiza", title="Samsung Galaxy A55 5G 256GB", price=1800.0)]
        filter_quotes("Samsung Galaxy A55 5G 256GB", quotes, exclude_words=[], min_score=0.5,
                      exclude_stores=["olx"])
        self.assertEqual([q.accepted for q in quotes], [False, True])

    def test_coupon_only_post_is_ignored(self):
        post = ("🔥 Meia Noite tem cupom Shopee\n\n- Cupons até R$100 OFF, saem meia noite e renovam durante o dia\n\n"
                "https://s.shopee.com.br/1qcmjnpcdU")
        self.assertIsNone(parse_post(post))

    def test_catalog_ssd_is_spec_based(self):
        from promo.catalog import get_catalog
        cat = get_catalog()
        p = cat.profile("SSD M.2 NVMe 1TB Western Digital WD Green SN350, Leitura 3200MB/s")
        self.assertEqual(p.category.key, "ssd")
        self.assertEqual(p.specs, {"interface_ssd": "nvme", "capacidade": 1024})
        self.assertEqual(p.query, "SSD NVMe 1TB")
        self.assertGreater(cat.equivalent(p, "SSD Kingston NV3, 1TB, M.2 2280, PCIe 4.0 x4, NVMe"), 0)
        self.assertEqual(cat.equivalent(p, "SSD Kingston A400, 960GB, SATA"), 0)
        self.assertEqual(cat.equivalent(p, "SSD Kingston NV3 2TB NVMe"), 0)
        self.assertEqual(cat.equivalent(p, "Case Gaveta para SSD M.2 NVMe"), 0)

    def test_catalog_monitor_similar_size(self):
        from promo.catalog import get_catalog
        cat = get_catalog()
        p = cat.profile("Monitor Gamer SuperFrame Ace, 21.45 Pol, Full HD, IPS, 120Hz")
        self.assertEqual(p.mode, "especificacao")
        self.assertGreater(cat.equivalent(p, 'Monitor LG 21.5" LED Full HD HDMI'), 0)
        self.assertEqual(cat.equivalent(p, "Monitor Gamer SuperFrame View 27 Pol Full HD 180Hz"), 0)

    def test_catalog_top_product_model(self):
        from promo.catalog import get_catalog
        cat = get_catalog()
        p = cat.profile("Placa de Vídeo Gigabyte RTX 5060 Windforce OC 8GB GDDR7")
        self.assertEqual(p.product.nome, "RTX 5060")
        self.assertEqual(p.liquidez, "alta")
        self.assertTrue(cat.matches_product(p.product, "Placa de Vídeo MSI GeForce RTX 5060 Ventus 2X 8G"))
        self.assertFalse(cat.matches_product(p.product, "Placa de Vídeo ASUS RTX 5060 Ti 16GB"))
        self.assertEqual(cat.profile("Apple iPhone 16 Pro 256GB").product.nome, "iPhone 16 Pro")
        self.assertEqual(cat.profile("Console PlayStation 5 Slim Digital 1TB").product.nome,
                         "PlayStation 5 Slim Digital")

    def test_catalog_rules_on_candidates(self):
        from promo.catalog import get_catalog
        cat = get_catalog()
        cpu = cat.profile("Processador AMD Ryzen 7 9800X3D, 4.7GHz, AM5")
        # "sem cooler" na ficha técnica não exclui; kit com placa-mãe no nome exclui
        self.assertFalse(cat.excluded(cpu.category, "Processador AMD Ryzen 7 9800X3D, 8 Cores, AM5, Sem Cooler"))
        mb = cat.profile("Placa-Mãe ASUS TUF GAMING A520M-PLUS II, AMD AM4")
        self.assertTrue(cat.excluded(mb.category, "Placa-Mãe ASUS TUF A520M-PLUS II + Processador AMD Ryzen 5 5500"))
        tela = cat.profile("100 Polegadas Tela para Projetor Bettdow, 16:9")
        self.assertEqual(tela.specs["tipo_tela"], "tecido")
        self.assertFalse(cat.spec_ok(tela, "tipo_tela", "Tela De Projeção Elétrica Grm, 100 Polegadas"))

    def test_reference_capped_by_cheapest_same_model(self):
        offer = Offer(title="Processador Ryzen 7 9800X3D", price=2159.0)
        quotes = [Quote("kabum", "Ryzen 7 9800X3D", p) for p in (2999.99, 3599.0, 3870.71)]
        a = analyze(offer, quotes, load_settings(), {"liquidez": "alta"})
        self.assertAlmostEqual(a.market_median, 3299.99, places=2)

    def test_quantity_in_post_and_listings(self):
        from promo.parser import extract_quantity, strip_quantity
        o = parse_post("5 Fones Bluetooth i12 TWS por R$ 50,00\nhttps://s.shopee.com.br/abc")
        self.assertEqual(o.quantity, 5)
        self.assertEqual(o.unit_price, 10.0)
        self.assertEqual(extract_quantity("Kit 3 Cabos USB-C 1m"), 3)
        self.assertEqual(extract_quantity("Pilha AA Duracell 4 unidades"), 4)
        # não confundir com memória 2x8GB, parcelas ou especificações
        self.assertEqual(extract_quantity("Memória Kit 2x8GB DDR4 3200MHz"), 1)
        self.assertEqual(extract_quantity("Smartphone 256GB 12x sem juros"), 1)
        self.assertEqual(extract_quantity("Smartphone Galaxy A56 3 câmeras 50MP"), 1)
        self.assertEqual(strip_quantity("Kit 5 Fones Bluetooth i12"), "Fones Bluetooth i12")
        # característica do produto não é quantidade
        self.assertEqual(extract_quantity(
            "Kabum -Gabinete Gamer Corsair 2000D RGB Airflow, Mini-ITX, 3x Cooler Fan RGB Slim, Branco"), 1)
        self.assertEqual(extract_quantity("Gabinete Gamer Rise Mode Galaxy Glass 3x Fan RGB"), 1)
        self.assertEqual(extract_quantity("Hub USB 4 Portas 3.0"), 1)
        self.assertEqual(extract_quantity("3x Cabo USB-C 1m Turbo"), 3)
        self.assertEqual(extract_quantity("Placa de Video MSI RTX5060TI 8g Shadow 2x Oc Plus 8gb"), 1)
        self.assertEqual(extract_quantity("Placa Mãe Gigabyte B760M D3HP DDR4 LGA 1700 4x DDR4"), 1)
        # kits verdadeiros
        self.assertEqual(extract_quantity("Shopee - Kit 3 Ventoinhas Rise Mode X 120mm ARGB"), 3)
        self.assertEqual(extract_quantity("Kit Energético Monster 473ml - 6 Unidades em oferta"), 6)
        self.assertEqual(extract_quantity("Kit 2x Pré Treino Diabo Verde 150g"), 2)
        self.assertEqual(extract_quantity("Kit de Fan Wovibo 6x unidades com Controladora"), 6)

    def test_min_profit_scales_per_unit(self):
        from promo.profit import min_profit_for
        rules = {"min_profit_base": 25, "min_profit_pct": 0.05}
        self.assertEqual(min_profit_for(10, rules), 25.5)
        self.assertEqual(min_profit_for(1000, rules), 75.0)
        offer = Offer(title="Fone Bluetooth i12", price=50.0, quantity=5, shipping_cost=0.0)
        a = analyze(offer, [Quote("kabum", "Fone Bluetooth i12", p) for p in (79.9, 85.0, 89.9)], load_settings())
        self.assertEqual(a.total_cost, 10.0)
        self.assertEqual(a.profile["quantidade"], 5)
        self.assertAlmostEqual(a.profile["lucro_total"], a.best.profit * 5, places=1)

    def test_group_rules_win_over_liquidity(self):
        offer = Offer(title="Fone Bluetooth X", price=100.0, shipping_cost=0.0)
        quotes = [Quote("kabum", "Fone Bluetooth X", p) for p in (170.0, 175.0, 180.0)]
        settings = load_settings()
        base = analyze(offer, quotes, settings, {"liquidez": "alta"})
        self.assertTrue(base.is_profitable)
        strict = analyze(offer, quotes, settings, {"liquidez": "alta", "grupo": {
            "name": "Fones", "min_profit_base": 100, "min_profit_pct": 0, "min_roi": None}})
        self.assertFalse(strict.is_profitable)
        self.assertTrue(any("abaixo do mínimo R$ 100.00" in r for r in strict.reasons))

    def test_false_positives_found_in_production(self):
        from promo.catalog import get_catalog
        cat = get_catalog()

        def accepted(offer_title, candidates):
            prof = cat.profile(offer_title)
            qs = [Quote("kabum", t, 500.0) for t in candidates]
            filter_quotes(offer_title, qs, exclude_words=[], min_score=0.6, profile=prof, catalog=cat)
            return [q.title for q in qs if q.accepted]

        # fone x celular/notebook da mesma marca
        self.assertEqual(accepted("Samsung Galaxy Buds Core, Fone de Ouvido, sem fio", [
            "Smartphone Samsung Galaxy A06, 128GB, 4GB RAM, Octa Core",
            "Notebook Samsung Galaxy Book4, Intel Core I3"]), [])
        # outro jogo da mesma série
        self.assertEqual(accepted("Jogo The Legend of Zelda: Ocarina of Time, Nintendo Switch 2", [
            "Jogo The Legend Of Zelda Tears Of The Kingdom Nintendo Switch"]), [])
        # outra marca, mesmo "modelo" (TC3)
        self.assertEqual(accepted("Cadeira Gamer TGT Heron TC3, Preto e Vermelho", [
            "Cadeira Gamer TC3 ThunderX3, Suporta até 150Kg"]), [])
        self.assertEqual(accepted("Câmera de Segurança Elsys Wi-Fi, Full HD", [
            "Câmera De Segurança Wi-fi Full Hd Im1 - 4560071, Intelbras"]), [])
        # Huawei Band 10 não é o produto top Xiaomi Smart Band 10
        self.assertIsNone(cat.profile("Smartwatch Huawei Band 10").product)
        # mesmo produto continua passando
        self.assertEqual(len(accepted("Controle Sem fio EasySMX X05 Versão Pro", [
            "Easysmx X05 Pro Controle Gamer Sem Fio Hall Para PC Switch"])), 1)

    def test_capacity_variants_and_ram(self):
        from promo.catalog import spec_capacidade
        from promo.matcher import normalize
        self.assertEqual(spec_capacidade(normalize("SSD NVME 512GB 1TB KOOTION M.2 Gen3")), 512)
        self.assertEqual(spec_capacidade(normalize("Xiaomi Poco X8 5g 512GB / 256GB Nfc")), 256)
        self.assertEqual(spec_capacidade(normalize("Celular Samsung Galaxy A17, 128GB, 4GB, 50MP")), 128)
        self.assertEqual(spec_capacidade(normalize("Notebook Lenovo 16GB RAM 512GB SSD")), 512)
        self.assertEqual(spec_capacidade(normalize("Pendrive Sandisk 8GB")), 8)

    def test_price_far_below_market_is_not_profitable(self):
        offer = Offer(title="iPhone 17 Pro Max 256GB incluso seguro", price=469.0, shipping_cost=0.0)
        a = analyze(offer, [Quote("kabum", "iPhone 17 Pro Max 256GB", p) for p in (9800, 9900, 9999)], load_settings())
        self.assertFalse(a.is_profitable)
        self.assertTrue(a.profile.get("verificar"))

    def test_second_opinion_reuse_keys_by_group(self):
        from promo.catalog import get_catalog
        cat = get_catalog()
        key = lambda t: cat.reuse_key(cat.profile(t), t)[0]
        # SSD: marca não importa
        self.assertEqual(key("SSD Kingston NV3 1TB M.2 NVMe"), key("SSD WD Green SN350 1TB NVMe M.2"))
        self.assertNotEqual(key("SSD Kingston NV3 1TB NVMe"), key("SSD Kingston NV3 2TB NVMe"))
        # placa de vídeo: mesmo modelo, qualquer fabricante de placa
        self.assertEqual(key("Placa de Vídeo MSI RTX 5060 Ventus 8GB"), key("Placa de Vídeo Gigabyte RTX 5060 Windforce 8GB"))
        self.assertNotEqual(key("Placa de Vídeo MSI RTX 5060 8GB"), key("Placa de Vídeo MSI RTX 5070 12GB"))
        # celular e fone: cada marca/modelo é uma busca
        self.assertNotEqual(key("Smartphone Samsung Galaxy A56 256GB"), key("Smartphone Motorola Moto G85 256GB"))
        self.assertNotEqual(key("Fone JBL Tune 520BT"), key("Fone Edifier W820NB"))
        self.assertEqual(key("Fone JBL Tune 520BT Preto"), key("Fone de Ouvido JBL Tune 520BT Azul"))

    def test_compatible_kabum_never_requests_paid_confirmation(self):
        import asyncio
        from promo.pipeline import Pipeline
        from promo.storage import SQLiteRepository

        class FakePaid:
            name = "google_shopping"
            calls = 0
            async def search(self, client, query):
                FakePaid.calls += 1
                return [Quote("google_shopping", "SSD Kingston NV3 1TB NVMe", p, store=s)
                        for p, s in ((950, "Magalu"), (960, "Amazon"), (970, "Mercado Livre"))]

        class FakeFree:
            name = "kabum_fake"
            async def search(self, client, query):
                return [Quote("kabum", f"{query} M.2 Genérico", p, store="KaBuM!") for p in (1300, 1310, 1320)]

        class Silent:
            async def send(self, text): return True

        settings = load_settings()
        settings.raw["rules"]["segunda_opiniao_limite_diario"] = 1
        pipe = Pipeline(settings, SQLiteRepository(":memory:"), Silent())
        pipe.sources, pipe.reserve = [FakeFree()], [FakePaid()]

        async def run(title, price):
            return await pipe.evaluate(Offer(title=title, price=price, shipping_cost=0.0))

        a1 = asyncio.run(run("SSD Kingston NV3 1TB NVMe M.2", 700.0))
        self.assertNotIn("segunda_opiniao", a1.profile)
        self.assertEqual(len(a1.accepted_quotes), 3)          # Apenas KaBuM: não consulta a próxima fonte.
        a2 = asyncio.run(run("SSD Crucial P3 1TB NVMe", 705.0))  # outra marca, mesma especificação
        self.assertNotIn("segunda_opiniao", a2.profile)
        a3 = asyncio.run(run("SSD Kingston NV3 2TB NVMe", 900.0))  # outra chave, limite 1/dia já usado
        self.assertNotIn("segunda_opiniao", a3.profile)
        self.assertEqual(FakePaid.calls, 0)
        # A referência KaBuM encerra a sequência sem consulta paga.
        self.assertGreaterEqual(a1.market_median, 1300)

    def test_hasdata_fallback_when_free_source_finds_nothing(self):
        import asyncio
        from promo.pipeline import Pipeline
        from promo.storage import SQLiteRepository

        class Empty:
            name = "kabum_fake"
            async def search(self, client, query): return []

        class Paid:
            name = "google_shopping"
            async def search(self, client, query):
                return [Quote("google_shopping", "Fone JBL Tune 770NC Bluetooth Azul", p, store=s)
                        for p, s in ((432.99, "Dressinn"), (474.9, "Magalu"), (499.0, "Amazon"))]

        class Silent:
            async def send(self, text): return True

        pipe = Pipeline(load_settings(), SQLiteRepository(":memory:"), Silent())
        pipe.sources, pipe.reserve = [Empty()], [Paid()]
        a = asyncio.run(pipe.evaluate(Offer(title="JBL, Fone de Ouvido Bluetooth, Tune 770NC, Over Ear, Sem Fio",
                                            price=250.0, shipping_cost=0.0)))
        self.assertEqual(a.profile["fonte_mercado"], "google_shopping")
        self.assertEqual(len(a.accepted_quotes), 3)
        # Ausência de categoria não reativa a antiga segunda opinião.
        b = asyncio.run(pipe.evaluate(Offer(title="Jameson Whiskey Irlandês 750 ml", price=90.0)))
        self.assertNotIn("segunda_opiniao", b.profile)

    def test_amazon_style_titles_keep_the_model(self):
        from promo.catalog import get_catalog
        from promo.matcher import head
        cat = get_catalog()
        t = "JBL, Fone de Ouvido Bluetooth, Tune 770NC, Over Ear, Sem Fio, Com Cancelamento de Ruído Azul"
        self.assertIn("Tune 770NC", head(t))
        self.assertIn("770NC", cat.profile(t).query)
        self.assertIn("Quantum 100M2", cat.profile("JBL, Fone de Ouvido Com Fio, Quantum 100M2, Headset Gamer, Over Ear").query)
        self.assertIn("Wave Buds 2", cat.profile("JBL, Fone de Ouvido Sem Fio, JBL, Bluetooth, Wave Buds 2, Intra Auricular").query)
        self.assertIn("Stage 320", cat.profile("JBL, Caixa de Som, PartyBox Stage 320, Bluetooth, Portátil").query)
        # títulos normais não mudam
        self.assertEqual(head("Placa-Mãe ASUS TUF GAMING A520M-PLUS II, AMD AM4, mATX"), "Placa-Mãe ASUS TUF GAMING A520M-PLUS II")
        self.assertEqual(head("Monitor Gamer SuperFrame Ace, 21.45 Pol, Full HD"), "Monitor Gamer SuperFrame Ace")
        # e outro produto JBL não passa mais como o mesmo
        self.assertEqual(similarity(t, "Caixa de Som JBL Flip 6 Bluetooth"), 0.0)
        self.assertGreaterEqual(similarity(t, "Fone de Ouvido JBL Tune 770NC Bluetooth Preto"), 0.6)

    def test_alert_has_resale_search_links(self):
        from promo.alerts import format_opportunity
        offer = Offer(title="SSD WD Green SN350 1TB NVMe", price=700.0, shipping_cost=0.0)
        a = analyze(offer, [Quote("kabum", "SSD NVMe 1TB", p) for p in (1000, 1010, 1020)], load_settings(),
                    {"busca": "SSD NVMe 1TB", "liquidez": "alta"})
        text = format_opportunity(a)
        self.assertIn("https://www.olx.com.br/brasil?q=SSD%20NVMe%201TB", text)
        self.assertIn("https://www.facebook.com/marketplace/search/?query=SSD%20NVMe%201TB", text)

    def test_headline_and_store_prefix_are_not_titles(self):
        o = parse_post("CAIXA COMPACTA, SOM DE RESPEITO! 🚀\n\nMONDIAL Caixa Amplificada, Preto, 150W RMS, Bivolt - CM-150\n\n"
                       "🔥 Por: R$ 119,90 parcelado\n🛒 Link: https://link.amazon/B07PuDTa")
        self.assertTrue(o.title.startswith("MONDIAL Caixa Amplificada"))
        o = parse_post("Kabum -Gabinete Gamer Corsair 2000D RGB Airflow, Mini-ITX\n\nR$ 314,99\nhttps://kabum.com.br/x")
        self.assertTrue(o.title.startswith("Gabinete Gamer Corsair"))

    def test_aliexpress_post(self):
        o = parse_post(POST_ALI)
        self.assertEqual(o.price, 289.0)
        self.assertEqual(o.coupon, "ALIBR30")
        self.assertEqual(o.store, "AliExpress")
        self.assertIn("Kingston NV2 1TB", o.title)

    def test_not_an_offer(self):
        self.assertIsNone(parse_post(POST_NOT_OFFER))


class MatcherTests(unittest.TestCase):
    def test_same_product(self):
        s = similarity("Samsung Galaxy A55 5G 256GB", "Smartphone Samsung Galaxy A55 5G 256GB Azul Escuro")
        self.assertGreater(s, 0.7)

    def test_different_capacity_rejected(self):
        self.assertEqual(similarity("Samsung Galaxy A55 256GB", "Samsung Galaxy A55 128GB"), 0.0)

    def test_different_model_rejected(self):
        self.assertEqual(similarity("Samsung Galaxy A55 256GB", "Samsung Galaxy A35 256GB"), 0.0)

    def test_spacing_variants(self):
        self.assertGreater(similarity("Placa de Vídeo RTX 4060 8GB", "Placa de Video RTX4060 8GB Gigabyte"), 0.5)
        self.assertGreater(similarity("Galaxy A55 5G 256GB", "Samsung Galaxy A55 256GB"), 0.5)

    def test_accessory_filtered(self):
        quotes = [Quote("x", "Capa Samsung Galaxy A55 5G", 39.9),
                  Quote("x", "Samsung Galaxy A55 5G 256GB", 2100.0)]
        filter_quotes("Samsung Galaxy A55 5G 256GB", quotes, exclude_words=["capa"], min_score=0.5,
                      offer_price=1599)
        self.assertFalse(quotes[0].accepted)
        self.assertTrue(quotes[1].accepted)


class ProfitTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings()

    def test_profitable_offer(self):
        offer = Offer(title="Samsung Galaxy A55 5G 256GB", price=1599.0, shipping_cost=0.0)
        quotes = [Quote("g", "Samsung Galaxy A55 5G 256GB", p) for p in (2399, 2449, 2499, 2350, 2599, 2399)]
        a = analyze(offer, quotes, self.settings)
        self.assertTrue(a.is_profitable, a.reasons)
        self.assertGreater(a.best.profit, 50)
        # conferência manual do canal ML clássico
        ml = next(c for c in a.channels if c.channel == "mercadolivre_classico")
        sale = round(2424 * 0.95, 2)  # mediana de 6 preços = (2399 + 2449) / 2
        expected = sale - sale * 0.12 - 0 - 25 - sale * 0.06 - 5 - sale * 0.03 - 1599
        self.assertAlmostEqual(ml.profit, round(expected, 2), places=1)

    def test_not_enough_quotes(self):
        offer = Offer(title="X", price=100.0)
        a = analyze(offer, [Quote("g", "X", 300)], self.settings)
        self.assertFalse(a.is_profitable)

    def test_bad_margin(self):
        offer = Offer(title="X", price=190.0)
        quotes = [Quote("g", "X", p) for p in (199, 205, 210)]
        self.assertFalse(analyze(offer, quotes, self.settings).is_profitable)

    def test_breakeven(self):
        cfg = self.settings.get("channels.mercadolivre_classico")
        be = breakeven_price(cfg, self.settings, 1000.0)
        offer = Offer(title="X", price=1000.0, shipping_cost=0)
        # vender exatamente no breakeven (sem desconto) deve dar lucro ~0
        self.settings.raw["rules"]["sale_discount_vs_median"] = 0
        a = analyze(offer, [Quote("g", "X", be)] * 3, self.settings)
        ml = next(c for c in a.channels if c.channel == "mercadolivre_classico")
        self.assertAlmostEqual(ml.profit, 0, delta=0.05)


if __name__ == "__main__":
    unittest.main()
