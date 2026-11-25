"""Decide se um resultado de busca é o MESMO produto da oferta.

Ideia: o que identifica um produto são marca, código de modelo (A55, RTX 4060,
SM-A556E) e variação (256GB, 8GB RAM, 1TB, 55"). Esses tokens pesam mais que
palavras comuns. Se a capacidade/variação divergir, o resultado é descartado.
"""
from __future__ import annotations

import re
import statistics
import unicodedata

from .models import Quote

STOPWORDS = {
    "de", "da", "do", "das", "dos", "e", "com", "para", "pra", "em", "a", "o", "as", "os",
    "novo", "nova", "original", "lacrado", "oferta", "promocao", "promo", "frete", "gratis",
    "envio", "imediato", "full", "cor", "modelo", "kit", "unidade", "un", "versao", "global",
    "nacional", "brasil", "garantia", "nf", "nota", "fiscal", "loja", "oficial", "-", "+",
}
CAPACITY_RE = re.compile(r"\b(\d+(?:[.,]\d+)?)\s?(tb|gb|mb|mah|w|hz|pol|polegadas|\"|ml|l|kg|btus?)(?![a-z0-9])")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("”", '"').replace("″", '"').replace("''", '"')
    return re.sub(r"[^\w\s\"\-.,]", " ", text)


def capacities(text: str) -> set[str]:
    out = set()
    for num, unit in CAPACITY_RE.findall(normalize(text)):
        unit = {"polegadas": "pol", '"': "pol", "btu": "btus"}.get(unit, unit)
        out.add(f"{num.replace(',', '.')}{unit}")
    return out


def tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalize(text))
    return {w for w in words if w not in STOPWORDS and len(w) > 1}


UNIT_TOKEN_RE = re.compile(
    r"^\d+(?:[.,]\d+)?(tb|gb|mb|mah|w|hz|ghz|mhz|pol|ml|l|kg|btus?|g|mm|cm|m|v|a|x|mbs|gbps|mbps)$")
HEAD_SPLIT_RE = re.compile(r",|\||\(|\s[-–]\s")


def head(title: str, max_words: int = 10) -> str:
    """Nome do produto sem a ficha técnica: até a 1ª vírgula/parêntese, no máx. 10 palavras.

    Posts trazem "Placa-Mãe ASUS TUF A520M-PLUS II, AMD AM4, DDR4, 90MB1..." e o
    Google Shopping devolve títulos curtos; só os códigos do começo são obrigatórios.

    Formato Amazon começa com a marca sozinha: "JBL, Fone de Ouvido Bluetooth, Tune 770NC,
    Over Ear..." -> junta os trechos seguintes até o que tem o modelo (o 1º com número).
    """
    parts = [p.strip() for p in HEAD_SPLIT_RE.split(title) if p and p.strip()]
    if not parts:
        return ""
    name = parts[0]
    if len(name.split()) <= 2 and not re.search(r"\d", name):
        for part in parts[1:5]:
            name += " " + part
            if re.search(r"\d", part) or len(name.split()) >= 14:
                break
        max_words = max(max_words, 14)
    return " ".join(name.split()[:max_words])


def model_codes(toks: set[str]) -> set[str]:
    """Tokens que misturam letras e números (a55, rtx4060, sm-a556e, i5-12400f).

    Ignora capacidades/unidades (256gb, 5g, 65w), que são tratadas à parte.
    """
    return {t for t in toks if re.search(r"\d", t) and re.search(r"[a-z]", t)
            and not UNIT_TOKEN_RE.match(t)}


def similarity(offer_title: str, candidate_title: str) -> float:
    a, b = tokens(offer_title), tokens(candidate_title)
    if not a or not b:
        return 0.0

    cap_a, cap_b = capacities(offer_title), capacities(candidate_title)
    if cap_a and cap_b and not (cap_a & cap_b):
        return 0.0  # 128GB vs 256GB -> produto diferente

    codes_a, codes_b = model_codes(a), model_codes(b)
    numbers_a = {t for t in a if t.isdigit() and len(t) >= 2}
    required = model_codes(tokens(head(offer_title)))
    if required:
        # todo código de modelo do NOME da oferta precisa aparecer no candidato
        # (aceita "rtx 4060" vs "rtx4060" juntando palavras vizinhas, mas não "b650" em "b650m")
        seq = [w.replace("-", "") for w in re.findall(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalize(candidate_title))]
        joined = set(seq) | {x + y for x, y in zip(seq, seq[1:])} | {x + y + z for x, y, z in zip(seq, seq[1:], seq[2:])}
        for code in required:
            if code.replace("-", "") not in joined:
                return 0.0

    name_toks = tokens(head(offer_title)) or a
    weights = {t: (3.0 if t in codes_a or t in numbers_a else 1.0) for t in name_toks}
    covered = sum(w for t, w in weights.items() if t in b)
    recall = covered / sum(weights.values())          # quanto do nome da oferta aparece no candidato
    precision = len(a & b) / len(b)                    # quanto o candidato é "só isso"
    score = 0.75 * recall + 0.25 * precision
    if cap_a and cap_a & cap_b:
        score = min(1.0, score + 0.1)
    # Palavras que dão nome ao produto ("Ocarina", "Heron", "Buds") faltando no candidato:
    # 1 = desconto; 2+ = outro produto ("Zelda Ocarina of Time" x "Zelda Tears of the Kingdom")
    flat_b = re.sub(r"[\s\-]", "", normalize(candidate_title))
    missing = [t for t in name_toks if t.isalpha() and len(t) >= 4 and t not in GENERIC_NAME_WORDS
               and t not in b and t not in flat_b]
    if len(missing) >= 2:
        return 0.0
    if missing:
        score -= 0.1
    return round(max(score, 0.0), 3)


# Palavras de nome que NÃO identificam o produto (tipo, cor, adjetivos de anúncio)
GENERIC_NAME_WORDS = {
    "preto", "preta", "branco", "branca", "azul", "vermelho", "vermelha", "rosa", "cinza", "prata", "dourado",
    "dourada", "verde", "roxo", "roxa", "amarelo", "amarela", "grafite", "black", "white", "blue", "pink",
    "gamer", "gaming", "smart", "wireless", "bluetooth", "fio", "novo", "nova", "original", "oficial", "edicao",
    "versao", "global", "nacional", "importado", "lancamento", "promocao", "oferta", "barato", "melhor",
    "smartphone", "celular", "fone", "ouvido", "placa", "video", "processador", "monitor", "cadeira",
    "escritorio", "camera", "seguranca", "controle", "jogo", "console", "teclado", "mouse", "headset", "headphone",
    "caixa", "notebook", "tablet", "relogio", "smartwatch", "pulseira", "inteligente", "memoria", "fonte",
    "gabinete", "ventoinha", "ventoinhas", "cooler", "aspirador", "eletrico", "eletrica", "portatil", "externo",
    "interno", "digital", "mecanico", "mecanica", "optico", "ergonomica", "ergonomico", "giratoria", "ajustavel",
    "garantia", "envio", "imediato", "loja", "vendido", "pela", "unidade", "tamanho", "modelo", "geracao",
}


def filter_quotes(offer_title: str, quotes: list[Quote], *, exclude_words: list[str],
                  min_score: float, offer_price: float | None = None,
                  exclude_stores: list[str] | None = None, offer_store: str | None = None,
                  profile=None, catalog=None, accept_used: bool = False) -> list[Quote]:
    """Marca `accepted`, `match_score` e `equivalent` em cada cotação.

    Com `profile`/`catalog` (promo.catalog): produto top aceita qualquer anúncio do
    mesmo produto; categoria por especificação aceita equivalentes de outra marca.
    """
    offer_norm = normalize(offer_title)
    excl = [normalize(w).strip() for w in exclude_words]
    bad_stores = [normalize(s).strip() for s in exclude_stores or []]
    own_store = normalize(offer_store or "").strip().rstrip("!")
    for q in quotes:
        q.match_score = similarity(offer_title, q.title)
        # JBL reutiliza números entre linhas diferentes: Tune 770NC != Live 770NC.
        families = {"tune", "live", "quantum", "wave", "endurance", "reflect", "club", "tour"}
        offer_family = tokens(offer_title) & families if "jbl" in tokens(offer_title) else set()
        candidate_family = tokens(q.title) & families
        other_jbl_family = bool(offer_family and candidate_family and not offer_family & candidate_family)
        q.equivalent = False
        if profile is not None and catalog is not None:
            specs_ok = all(catalog.spec_ok(profile, name, q.title) for name in profile.specs)
            cand_cat = catalog.category_of(q.title)
            other_category = bool(profile.category and cand_cat and cand_cat is not profile.category)
            # modo "mesmo modelo" sem produto top: marca diferente = outro produto
            # (produto top tem os próprios termos; RTX 5060 da MSI ou da Gigabyte é o mesmo)
            other_brand = False
            if profile.mode == "modelo" and not profile.product and profile.brand:
                cand_brand = catalog.brand_of(q.title)
                other_brand = bool(cand_brand and cand_brand != profile.brand)
            if not specs_ok or other_category or other_brand:
                q.match_score = 0.0  # tamanho/capacidade/tipo diferente, outra categoria ou marca
            elif q.match_score < min_score:
                if profile.mode == "especificacao":
                    score = catalog.equivalent(profile, q.title)
                    if score > 0:
                        q.match_score, q.equivalent = score, True
                elif profile.product and catalog.matches_product(profile.product, q.title):
                    q.match_score = max(q.match_score, 0.8)
        title_norm = normalize(q.title)
        allowed_condition_words = {"usado", "usada", "seminovo", "seminova"} if accept_used and q.condition == "used" else set()
        bad_word = any(w and w not in allowed_condition_words and w in title_norm and w not in offer_norm for w in excl)
        # candidato cai nas exclusões da categoria (kit placa-mãe + processador, capa...)
        if profile is not None and catalog is not None and profile.category is not None:
            bad_word = bad_word or catalog.excluded(profile.category, q.title, offer_title)
        store_norm = normalize(q.store or "")
        bad_store = any(s and s in store_norm for s in bad_stores)
        # o próprio anúncio da promoção (mesma loja, mesmo preço) não é "mercado"
        is_offer_itself = bool(own_store and offer_price and own_store in store_norm
                               and abs(q.price - offer_price) <= 0.02 * offer_price)
        q.accepted = (q.match_score >= min_score and not bad_word and not bad_store
                      and not other_jbl_family and not is_offer_itself and q.price > 0)

    # Descarta preços absurdos (outliers) entre os aceitos
    accepted = [q for q in quotes if q.accepted]
    if len(accepted) >= 4:
        prices = sorted(q.price for q in accepted)
        med = statistics.median(prices)
        for q in accepted:
            if q.price < med * 0.45 or q.price > med * 2.2:
                q.accepted = False
    # Um "mesmo produto" custando 1/3 da oferta quase sempre é acessório ou golpe
    if offer_price:
        for q in quotes:
            if q.accepted and q.price < offer_price * 0.35:
                q.accepted = False
    return quotes
