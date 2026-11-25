import unittest
from promo.parser import parse_post,is_promotional_only_post
from promo.title_filter import clean_title
from promo.catalog import get_catalog
from promo.config import load_settings
from promo.pipeline import Pipeline
from promo.storage import SQLiteRepository

SHOE='''O TÊNIS CORINGA
combina com tudo
Tênis Casual Kappa Zenit Masculino Unissex Conforto (35 ao 43)
De R$ 229 Por R$ 96
Loja Oficial Kappa no ML
https://meli.la/teste'''
TV='''BAIXOU A NANO AI UHD DA LG, MODELO 4K COM 55"
Smart TV LG 55" 4K UHD NANO AI 55UN85CBPSA.BWZ
De: R$ 2.763 Por: R$ 2.299 no pix
Loja Oficial Vikings na Amazon:
https://link.amazon/teste'''

class TitleFilterTests(unittest.TestCase):
    def test_real_shoe_post_uses_product_line(self):
        offer=parse_post(SHOE)
        self.assertTrue(offer.title.startswith('Tênis Casual Kappa Zenit'))
        self.assertEqual(offer.price,96)
        self.assertEqual(offer.raw_text,SHOE)

    def test_real_tv_post_keeps_model_and_category(self):
        offer=parse_post(TV)
        self.assertEqual(offer.title,'Smart TV LG 55" 4K UHD NANO AI 55UN85CBPSA.BWZ')
        self.assertEqual(offer.price,2299)
        profile=get_catalog().profile(offer.title)
        self.assertEqual(profile.category.key,'tv')
        self.assertIn('55UN85CBPSA.BWZ',profile.query)
        self.assertEqual(profile.query.lower().count('4k'),1)
        self.assertNotIn('BAIXOU',profile.query)

    def test_slogans_alone_do_not_become_products(self):
        for phrase in ['Combina tudo','combina com tudo','ESSE COMBINA COM TUDO','SUPER PROMOÇÃO!']:
            post=phrase+'\nR$ 199,90\nhttps://loja.example/item'
            self.assertIsNone(parse_post(post),phrase)
            self.assertTrue(is_promotional_only_post(post),phrase)

    def test_slogan_with_real_product_is_preserved(self):
        offer=parse_post('SUPER PROMOÇÃO!\nFone JBL Tune 520BT Azul\nR$ 189,90')
        self.assertEqual(offer.title,'Fone JBL Tune 520BT Azul')

    def test_prefix_is_removed_from_same_line(self):
        self.assertEqual(parse_post('BAIXOU! Fone JBL Tune 520BT\nR$ 189,90').title,'Fone JBL Tune 520BT')

    def test_technical_names_are_not_stopwords(self):
        title='Apple iPad Air 5 Pro Max 256GB'
        self.assertEqual(clean_title(title),title)
        self.assertEqual(clean_title('Super Mario Bros Wonder Nintendo Switch'),'Super Mario Bros Wonder Nintendo Switch')

    def test_description_phrase_does_not_block_whole_post(self):
        offer=parse_post('Camisa Masculina Kappa que combina com tudo\nR$ 99,90')
        self.assertIsNotNone(offer)
        self.assertIn('Camisa Masculina Kappa',offer.title)

    def test_brand_with_link_is_not_a_link_metadata_line(self):
        self.assertIn('TP-Link',parse_post('Roteador TP-Link Archer AX12 Wi-Fi 6\nR$ 199,90').title)

    def test_ai_cannot_resurrect_obvious_slogan(self):
        class NeverAI:
            def parse(self,text):raise AssertionError('Slogan não pode chamar IA')
        pipe=Pipeline(load_settings(),SQLiteRepository(':memory:'),None)
        pipe.ai=NeverAI()
        self.assertIsNone(pipe.extract('Combina tudo\nR$ 199,90\nhttps://loja.example'))

    def test_unknown_product_is_not_rejected_just_for_missing_catalog(self):
        offer=parse_post('Lanterna Acme XZ900\nR$ 79,90')
        self.assertEqual(offer.title,'Lanterna Acme XZ900')

    def test_custom_exclusions_apply_to_whole_headline(self):
        rules={'ignored_headlines':['Vai que é sua']}
        self.assertIsNone(parse_post('Vai que é sua\nR$ 99,90',title_rules=rules))
        self.assertEqual(parse_post('Vai que é sua\nSSD Kingston NV3 1TB\nR$ 299,90',title_rules=rules).title,'SSD Kingston NV3 1TB')


if __name__=='__main__':unittest.main()
