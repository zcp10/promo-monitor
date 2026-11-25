"""Extrai produto, preço, cupom, loja e link de um post de promoção.

Primeiro tenta por regras (rápido e grátis). Se não conseguir título + preço,
o chamador pode usar `ai_parse` (Claude) como plano B.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from .models import Offer
from .title_filter import clean_title

PRICE_RE = re.compile(r"R\$\s*([\d.]+(?:,\d{1,2})?)", re.IGNORECASE)
BARE_PRICE_RE = re.compile(r"\b(?:por|sai\s+por|apenas|só)\s*:?\s*([\d.]+,\d{2})\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s<>()\"']+", re.IGNORECASE)
COUPON_RE = re.compile(
    r"(?:cupom|cupon|c[óo]digo|code)\s*(?:de\s+desconto)?\s*[:\-➡️👉→]*\s*[\"'`]?([A-Z0-9][A-Z0-9_\-]{3,24})",
    re.IGNORECASE,
)
INSTALLMENT_RE = re.compile(r"\d{1,2}\s*x\s*(?:de\s*)?R\$\s*[\d.,]+", re.IGNORECASE)
# "R$ 100 OFF", "até R$ 50 de desconto", "R$20 de cashback": valor do cupom, não preço
DISCOUNT_RE = re.compile(r"(?:at[ée]\s+)?R\$\s*[\d.,]+\s*(?:off\b|de\s+desconto|de\s+cashback|de\s+volta)",
                         re.IGNORECASE)
# Título que fala só de cupom/cashback ("Meia noite tem cupom Shopee"): não é produto
COUPON_ONLY_RE = re.compile(r"^\W*(?:\w+\s+){0,4}(?:cupo(?:m|ns|n)|cashback|frete gr[áa]tis)\b", re.IGNORECASE)
SHIPPING_RE = re.compile(r"frete\s*(?:de|:)?\s*R\$\s*([\d.]+(?:,\d{1,2})?)", re.IGNORECASE)
FREE_SHIPPING_RE = re.compile(r"frete\s+gr[áa]tis", re.IGNORECASE)
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0000FE0F\U0000200D\U00002190-\U000021FF"
    "\U00002B00-\U00002BFF\U00003030\U0000303D\U00003297\U00003299]+"
)

FINAL_PRICE_HINTS = ("por r$", "sai por", "à vista", "a vista", "no pix", "com cupom",
                     "preço final", "valor final", "fica", "por apenas", "💰", "🔥 r$")
OLD_PRICE_HINTS = ("de r$", "antes", "era r$", "~~")
GENERIC_WORDS = {"oferta", "ofertas", "promoção", "promocao", "promo", "relâmpago", "relampago",
                 "imperdível", "imperdivel", "baixou", "corre", "corra", "achadinho", "achado",
                 "urgente", "bug", "preço", "preco", "mínimo", "minimo", "histórico", "historico",
                 "hoje", "aproveite", "só", "so", "agora", "menor", "!!", "alerta", "de", "do", "o"}

STORES = [
    (("mercadolivre", "mercadolibre", "meli.la", "mlb"), "Mercado Livre"),
    (("shopee",), "Shopee"),
    (("aliexpress", "ali.express"), "AliExpress"),
    (("amazon.", "amzn."), "Amazon"),
    (("magazineluiza", "magalu"), "Magazine Luiza"),
    (("kabum",), "KaBuM!"),
    (("pichau",), "Pichau"),
    (("terabyte",), "Terabyte"),
    (("americanas",), "Americanas"),
    (("casasbahia",), "Casas Bahia"),
    (("carrefour",), "Carrefour"),
    (("fastshop",), "Fast Shop"),
]
STORE_TEXT_HINTS = {
    "mercado livre": "Mercado Livre", "shopee": "Shopee", "aliexpress": "AliExpress",
    "amazon": "Amazon", "magalu": "Magazine Luiza", "magazine luiza": "Magazine Luiza",
    "kabum": "KaBuM!", "pichau": "Pichau", "terabyte": "Terabyte",
}


# Quantidade no título/post ("Kit 5 fones", "5 unidades", "3x Cabo", "Pack com 4")
COUNT_NOUNS = (r"fones?|mouses?|cabos?|carregadores?|pilhas?|baterias?|lampadas?|pendrives?|"
               r"cartoes?|controles?|teclados?|garrafas?|copos?|meias?|camisetas?|cuecas?|"
               r"toalhas?|lenco[s]?|escovas?|pecas?|itens?|caixas de som|smartwatches?|relogios?")
# "Kit 3 ventoinhas", "Kit 2x Pré Treino" (mas não "Kit 2x8GB" de memória)
KIT_RE = re.compile(r"\b(?:kit|pack|combo|conjunto|caixa)\s*(?:com|c/|de)?\s*(\d{1,3})(?:x\b|\b)(?!\s*x?\s*\d)(?![.,]\d)")
# "6 unidades", "6x unidades", "10 peças": vale em qualquer parte do título
UNITS_RE = re.compile(r"\b(\d{1,3})\s*x?\s*(?:unidades|unidade|unid|und|un|pecas|pcs|pares)\b")
# "3x Fone ...", "5 fones ...": só no COMEÇO; no meio é característica ("Gabinete ... 3x Cooler Fan")
START_RES = [re.compile(r"^\W*(\d{1,3})\s*x\s+(?!de\b|sem\b|no\b|r\$)(?=[a-z])"),
             re.compile(r"^\W*(\d{1,3})\s+(?:" + COUNT_NOUNS + r")\b")]
# "Shopee - ", "Shopee / Pichau - ", "Kabum -" no começo do post
STORE_PREFIX_RE = re.compile(
    r"^\W*(?:(?:shopee|kabum|amazon|pichau|terabyte|magalu|mercado ?livre|aliexpress|regional)\s*[/|,]?\s*)+[-–:]\s*")
QTY_STRIP_RE = re.compile(
    r"\b(?:kit|pack|combo|conjunto)\s*(?:com|c/|de)?\s*\d{1,3}(?:x\b|\b)(?!\s*x?\s*\d)\s*(?:unidades|unid|un|pecas|pcs)?"
    r"|\b\d{1,3}\s*x?\s*(?:unidades|unidade|unid|und|un|pecas|pcs|pares)\b"
    r"|^\W*\d{1,3}\s*x\s+(?!de\b|sem\b|no\b)(?=[a-z])", re.IGNORECASE)


def extract_quantity(text: str) -> int:
    """Quantas unidades o anúncio/post vende juntas (1 se não disser).

    "Kit N" só no nome do produto (antes da 1ª vírgula/traço) e "3x"/"5 fones" só no
    começo: a ficha técnica tem números que não são quantidade ("3x Cooler Fan").
    "N unidades" vale em qualquer parte ("Monster 473ml - 6 Unidades").
    """
    from .matcher import head, normalize

    full = STORE_PREFIX_RE.sub("", normalize(text or ""))
    name = head(full, 14)
    checks = [(KIT_RE, name), (UNITS_RE, full)] + [(rx, name) for rx in START_RES]
    for rx, t in checks:
        m = rx.search(t)
        if m:
            n = int(m.group(1))
            if 2 <= n <= 200:
                return n
    return 1


def strip_quantity(title: str) -> str:
    """'Kit 5 Fones Bluetooth i12' -> 'Fones Bluetooth i12' (para buscar a unidade)."""
    return re.sub(r"\s+", " ", QTY_STRIP_RE.sub(" ", title)).strip(" -,")


def parse_brl(text: str) -> float | None:
    """'1.799,90' -> 1799.9 ; '1.799' -> 1799.0 ; '99,9' -> 99.9"""
    s = text.strip().rstrip(".")
    if not s:
        return None
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"\d{1,3}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        value = float(s)
    except ValueError:
        return None
    return value if 0 < value < 1_000_000 else None


def detect_store(urls: list[str], text: str) -> str | None:
    for url in urls:
        host = (urlparse(url).netloc or "").lower() + (urlparse(url).path or "").lower()[:20]
        for keys, name in STORES:
            if any(k in host for k in keys):
                return name
    low = text.lower()
    for hint, name in STORE_TEXT_HINTS.items():
        if hint in low:
            return name
    return None


def _clean_line(line: str) -> str:
    line = EMOJI_RE.sub(" ", line)
    line = re.sub(r"[*_~`#|>]+", " ", line)
    return re.sub(r"\s+", " ", line).strip(" -–:•·")


def _is_generic(line: str) -> bool:
    words = [w for w in re.findall(r"[\wÀ-ÿ!]+", line.lower())]
    if not words:
        return True
    meaningful = [w for w in words if w not in GENERIC_WORDS and not w.isdigit()]
    return len(meaningful) < 2


# "Shopee - ", "Kabum -", "Amazon - " antes do nome do produto (a loja não é a marca)
STORE_PREFIX_TITLE_RE = re.compile(
    r"^(?:(?:shopee|kabum!?|amazon|pichau|terabyte|magalu|mercado\s*livre|aliexpress|regional)\s*[/|,]?\s*)+[-–:]\s*",
    re.IGNORECASE)


def _is_headline(line: str) -> bool:
    """Chamada de marketing ("CAIXA COMPACTA, SOM DE RESPEITO!"), não nome de produto."""
    letters = [c for c in line if c.isalpha()]
    shouting = len(letters) >= 8 and sum(c.isupper() for c in letters) / len(letters) > 0.85
    return line.rstrip().endswith("!") or (shouting and not re.search(r"\d", line))


def extract_title(lines: list[str], title_rules: dict | None = None) -> str | None:
    """Escolhe a linha com mais evidência de produto, não a primeira frase do post."""
    from .catalog import get_catalog
    from .matcher import model_codes, tokens

    catalog = get_catalog()
    candidates = []
    metadata = re.compile(
        r"^(?:loja\b|vendido por\b|link\b|clique\b|grupo\b|canal\b|frete\b|"
        r"parcelamento\b|estoque\b|use (?:o )?cupom\b|cupom\b|cores?\b|"
        r"varias opcoes\b|várias opções\b)", re.IGNORECASE)
    for position, raw in enumerate(lines[:16]):
        cuts = [m.start() for rx in (PRICE_RE, BARE_PRICE_RE, URL_RE, COUPON_RE) if (m := rx.search(raw))]
        head = raw[:min(cuts)] if cuts else raw
        head = re.sub(r"\b(?:por|de|sai|apenas|só)\s*:?\s*$", "", head.strip(), flags=re.IGNORECASE)
        line = STORE_PREFIX_TITLE_RE.sub("", _clean_line(head))
        line = clean_title(line, title_rules)
        if len(line) < 6 or metadata.search(line):
            continue
        category = catalog.category_of(line)
        brand = catalog.brand_of(line)
        codes = model_codes(tokens(line))
        if _is_generic(line) and not category and not (brand and codes):
            continue
        # Modelo alfanumérico completo pesa mais que chamada genérica com marca/tamanho.
        score = (10 * bool(category) + 4 * bool(brand) + 7 * min(3, len(codes))
                 + 10 * bool(catalog.product_of(line)) - 2 * _is_headline(line))
        candidates.append((score, -position, line[:200]))
    return max(candidates)[2] if candidates else None


def is_promotional_only_post(text: str, title_rules: dict | None = None) -> bool:
    """Evita chamar outra IA para ressuscitar um slogan/cupom já descartado."""
    lines = [line for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return True
    if COUPON_ONLY_RE.search(_clean_line(lines[0])):
        return True
    if extract_title(lines, title_rules) is not None:
        return False
    return any(_clean_line(line) and not clean_title(_clean_line(line), title_rules)
               for line in lines)


def extract_prices(text: str) -> tuple[float | None, float | None]:
    """Retorna (preço_final, preço_antigo)."""
    cleaned = DISCOUNT_RE.sub(" ", INSTALLMENT_RE.sub(" ", text))
    final_candidates: list[float] = []
    old_candidates: list[float] = []
    all_prices: list[float] = []

    for line in cleaned.splitlines():
        low = line.lower()
        if "frete" in low and "grátis" not in low and "gratis" not in low:
            continue  # linha de frete não é preço do produto
        prices = [p for p in (parse_brl(m) for m in PRICE_RE.findall(line)) if p]
        prices += [p for p in (parse_brl(m) for m in BARE_PRICE_RE.findall(line)) if p]
        if not prices:
            continue
        all_prices.extend(prices)
        # "De R$ 2.499 por R$ 1.799" na mesma linha
        if re.search(r"\bde\s+r\$", low) and re.search(r"\bpor\b", low) and len(prices) >= 2:
            old_candidates.append(max(prices))
            final_candidates.append(min(prices))
            continue
        if "~~" in line or any(h in low for h in OLD_PRICE_HINTS):
            old_candidates.extend(prices)
        elif any(h in low for h in FINAL_PRICE_HINTS):
            final_candidates.extend(prices)

    if final_candidates:
        price = min(final_candidates)
    elif all_prices:
        non_old = [p for p in all_prices if p not in old_candidates] or all_prices
        price = min(non_old)
    else:
        return None, None

    old = max(old_candidates) if old_candidates else None
    if old is None and len(set(all_prices)) >= 2 and max(all_prices) <= price * 3:
        old = max(all_prices) if max(all_prices) > price else None
    if old is not None and old <= price:
        old = None
    return price, old


def parse_post(text: str, *, title_rules: dict | None = None) -> Offer | None:
    """Parser por regras. Retorna None se não achar título e preço."""
    if not text or not text.strip():
        return None
    lines = [l for l in text.splitlines() if l.strip()]
    if lines and COUPON_ONLY_RE.search(_clean_line(lines[0])):
        return None  # post de cupom, sem produto
    urls = URL_RE.findall(text)
    title = extract_title(lines, title_rules)
    price, old_price = extract_prices(text)
    if not title or price is None:
        return None

    coupon_match = COUPON_RE.search(text)
    coupon = coupon_match.group(1).upper() if coupon_match else None
    if coupon and coupon.lower() in {"desconto", "aplicado", "abaixo", "na", "pagina"}:
        coupon = None

    shipping = None
    if FREE_SHIPPING_RE.search(text):
        shipping = 0.0
    else:
        ship = SHIPPING_RE.search(text)
        if ship:
            shipping = parse_brl(ship.group(1))

    # quantidade: no título ou nas primeiras linhas ("5 fones por R$ 50")
    quantity = extract_quantity(title)
    if quantity == 1:
        quantity = extract_quantity(" ".join(lines[:3]))
    return Offer(
        title=title,
        price=price,
        old_price=old_price,
        coupon=coupon,
        store=detect_store(urls, text),
        url=urls[0].rstrip(".,)") if urls else None,
        shipping_cost=shipping,
        raw_text=text,
        quantity=quantity,
    )


# ----------------------------------------------------------------------
# Plano B: extração com Claude (opcional)
# ----------------------------------------------------------------------
AI_PROMPT = """Você extrai dados de posts de promoção brasileiros.
Responda APENAS com JSON válido neste formato:
{"is_product_offer": bool, "title": str|null, "price": number|null, "old_price": number|null,
 "coupon": str|null, "store": str|null, "shipping_cost": number|null, "quantity": int}
- "title": nome do produto com marca, modelo e variação (ex.: "Samsung Galaxy A55 5G 256GB").
- "price": preço FINAL que o comprador paga à vista (já com cupom, se o post disser).
- Não use valor de parcela como preço.
- "quantity": quantas unidades vêm pelo preço (ex.: "5 fones por R$ 50" -> 5); 1 se for um só.
- Se não for oferta de produto físico (curso, assinatura, sorteio), is_product_offer=false.

Post:
<<<
{text}
>>>"""


@dataclass
class AIParser:
    api_key: str
    model: str = "claude-haiku-5-5"

    def parse(self, text: str) -> Offer | None:
        import anthropic  # import tardio: só exige o pacote se a IA estiver ligada

        client = anthropic.Anthropic(api_key=self.api_key)
        msg = client.messages.create(
            model=self.model,
            max_tokens=400,
            messages=[{"role": "user", "content": AI_PROMPT.replace("{text}", text[:4000])}],
        )
        body = "".join(getattr(b, "text", "") for b in msg.content).strip()
        match = re.search(r"\{.*\}", body, re.DOTALL)
        if not match:
            return None
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            return None
        if not data.get("is_product_offer") or not data.get("title") or not data.get("price"):
            return None
        urls = URL_RE.findall(text)
        return Offer(
            title=str(data["title"])[:200],
            price=float(data["price"]),
            old_price=float(data["old_price"]) if data.get("old_price") else None,
            coupon=data.get("coupon"),
            store=data.get("store") or detect_store(urls, text),
            url=urls[0].rstrip(".,)") if urls else None,
            shipping_cost=float(data["shipping_cost"]) if data.get("shipping_cost") is not None else None,
            raw_text=text,
            quantity=max(1, min(200, int(data.get("quantity") or 1))),
        )
