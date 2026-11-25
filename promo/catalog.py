"""Catálogo de revenda: categoria, produto "top", especificações e equivalência.

Duas formas de comparar, definidas por categoria em config/categories.yaml:

- comparar: modelo         -> precisa ser o MESMO modelo (RTX 5060, iPhone 16, G203).
- comparar: especificacao  -> vale qualquer produto da mesma categoria com as mesmas
                              especificações (SSD NVMe 1TB de qualquer marca; monitor
                              21,5" Full HD). Opcional `mesma_marca: true` quando a marca
                              pesa no preço (air fryer, TV).

Especificações ausentes no anúncio comparado não eliminam (o título do Google/KaBuM
nem sempre traz tudo); especificações presentes e diferentes eliminam.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml
from .title_filter import clean_title

from .matcher import head, normalize

CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "categories.yaml"


# ----------------------------------------------------------------------
# Especificações
# ----------------------------------------------------------------------
def _num(s: str) -> float:
    return float(s.replace(",", "."))


STORAGE_STEPS = [4, 8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384]


def _storage_bucket(gb: float) -> int | None:
    """240→256, 480/500→512, 960/1000→1024 (1TB), 2000→2048."""
    for step in STORAGE_STEPS:
        if 0.85 * step <= gb <= 1.15 * step:
            return step
    return None


_NOT_STORAGE = r"(?!\s?(?:de\s)?(?:ram|memoria|ddr\d|lpddr\d|gddr\d|vram|cache|l3|l2))"


def spec_capacidade(t: str) -> int | None:
    vals = []
    for m in re.finditer(r"(\d+(?:[.,]\d+)?)\s?(tb|gb)\b" + _NOT_STORAGE, t):
        before = t[max(0, m.start() - 14):m.start()]
        # "RAM 16GB" é memória; em "16GB RAM 512GB" o RAM pertence ao 16GB, não ao 512GB
        if re.search(r"(ram|ddr\d|memoria)\s*(?:de\s*)?$", before) and not re.search(r"\d\s?gb\s?(?:de\s)?ram\s*$", before):
            continue
        vals.append(_num(m.group(1)) * (1024 if m.group(2) == "tb" else 1))
    if not vals:
        return None
    # "SSD 512GB 1TB", "Poco X8 512GB / 256GB": anúncio com variações; o preço é da menor.
    # Valores < 16GB ao lado de outros maiores são RAM ("Galaxy A17, 128GB, 4GB").
    big = [v for v in vals if v >= 16] or vals
    return _storage_bucket(min(big))


def spec_capacidade_ram(t: str) -> int | None:
    kit = re.search(r"(\d)\s?x\s?(\d{1,2})\s?gb", t)
    if kit:
        return int(kit.group(1)) * int(kit.group(2))
    m = re.search(r"(\d{1,3})\s?gb", t)
    return int(m.group(1)) if m else None


def spec_ram(t: str) -> int | None:
    m = re.search(r"(\d{1,2})\s?gb\s?(?:de\s)?(?:ram|memoria)", t) or \
        re.search(r"(?:ram|memoria)\s?(?:de\s)?(\d{1,2})\s?gb", t)
    return int(m.group(1)) if m else None


def spec_ddr(t: str) -> str | None:
    m = re.search(r"\b(?:lp)?(ddr\d)", t)
    return m.group(1) if m else None


def spec_mhz(t: str) -> int | None:
    m = re.search(r"(\d{4})\s?mhz", t) or re.search(r"\bddr\d[\s-]?(\d{4})\b", t)
    return int(m.group(1)) if m else None


def spec_formato_ram(t: str) -> str | None:
    if re.search(r"notebook|sodimm|so-dimm|laptop", t):
        return "notebook"
    if re.search(r"desktop|udimm|\bdimm\b|\bpc\b", t):
        return "desktop"
    return None


def spec_interface_ssd(t: str) -> str | None:
    if re.search(r"nvme|pcie|pci-e", t):
        return "nvme"
    if re.search(r"\bsata\b", t):
        return "sata"
    return None


def spec_polegadas(t: str) -> float | None:
    m = re.search(r"(\d{1,3}(?:[.,]\d{1,2})?)\s?(?:\"|pol\b|polegadas|pol\.)", t)
    if not m:
        return None
    v = _num(m.group(1))
    return v if 5 <= v <= 120 else None


def spec_hz(t: str) -> int | None:
    vals = [int(x) for x in re.findall(r"\b(\d{2,3})\s?hz\b", t)]
    vals = [v for v in vals if 50 <= v <= 600]
    return max(vals) if vals else None


def spec_resolucao(t: str) -> str | None:
    if re.search(r"\b(?:4k|uhd|2160p)\b", t):
        return "4k"
    if re.search(r"\b(?:qhd|wqhd|2k|1440p)\b", t):
        return "qhd"
    if re.search(r"full\s?hd|\bfhd\b|1080p", t):
        return "fhd"
    if re.search(r"\bhd\b|720p", t):
        return "hd"
    return None


def spec_watts(t: str) -> int | None:
    m = re.search(r"\b(\d{3,4})\s?w\b", t)
    return int(m.group(1)) if m else None


def spec_selo80plus(t: str) -> str | None:
    m = re.search(r"80\s?plus\s?(white|bronze|silver|gold|platinum|titanium)?", t)
    return (m.group(1) or "white") if m else None


def spec_mah(t: str) -> int | None:
    m = re.search(r"(\d{1,2}[.,]?\d{3})\s?mah", t)
    return int(m.group(1).replace(".", "").replace(",", "")) if m else None


def spec_litros(t: str) -> float | None:
    m = re.search(r"(\d{1,2}(?:[.,]\d)?)\s?(?:l\b|litros?\b|lts?\b)", t)
    return _num(m.group(1)) if m else None


def spec_btus(t: str) -> int | None:
    m = re.search(r"(\d{1,2}[.,]?\d{3})\s?btus?", t)
    return int(m.group(1).replace(".", "").replace(",", "")) if m else None


def spec_conexao(t: str) -> str:
    if re.search(r"sem\s?fio|wireless|bluetooth|lightspeed|2[.,]4\s?ghz|\bbt\b", t):
        return "sem_fio"
    return "com_fio"


def spec_tipo_tela(t: str) -> str:
    if re.search(r"eletric|motorizad", t):
        return "eletrica"
    if re.search(r"retratil|retratel|de parede|de teto", t):
        return "retratil"
    if re.search(r"tripe", t):
        return "tripe"
    return "tecido"  # dobrável / portátil / com ilhós: a mais barata


def spec_vram(t: str) -> int | None:
    m = re.search(r"(\d{1,2})\s?gb\s?(?:de\s)?(?:gddr\d|vram)", t)
    return int(m.group(1)) if m else None


@dataclass(frozen=True)
class Spec:
    extract: Callable[[str], Any]
    # Como comparar: exato, ou tolerância relativa/absoluta para números
    rel: float | None = None
    abs: float | None = None

    def same(self, a: Any, b: Any) -> bool:
        if self.abs is not None:
            return abs(float(a) - float(b)) <= self.abs
        if self.rel is not None:
            return min(a, b) / max(a, b) >= 1 - self.rel
        return a == b


SPECS: dict[str, Spec] = {
    "capacidade": Spec(spec_capacidade),
    "capacidade_ram": Spec(spec_capacidade_ram),
    "ram": Spec(spec_ram),
    "ddr": Spec(spec_ddr),
    "mhz": Spec(spec_mhz, rel=0.15),
    "formato_ram": Spec(spec_formato_ram),
    "interface_ssd": Spec(spec_interface_ssd),
    "polegadas": Spec(spec_polegadas, abs=1.0),
    "hz": Spec(spec_hz, rel=0.2),
    "resolucao": Spec(spec_resolucao),
    "watts": Spec(spec_watts, rel=0.1),
    "selo80plus": Spec(spec_selo80plus),
    "mah": Spec(spec_mah, rel=0.15),
    "litros": Spec(spec_litros, abs=0.6),
    "btus": Spec(spec_btus),
    "conexao": Spec(spec_conexao),
    "vram": Spec(spec_vram),
    "tipo_tela": Spec(spec_tipo_tela),
}

SPEC_LABELS = {  # como cada especificação entra no texto de busca
    "capacidade": lambda v: f"{v // 1024}TB" if v >= 1024 else f"{v}GB",
    "capacidade_ram": lambda v: f"{v}GB",
    "ram": lambda v: f"{v}GB RAM",
    "ddr": lambda v: v.upper(),
    "mhz": lambda v: f"{v}MHz",
    "interface_ssd": lambda v: {"nvme": "NVMe", "sata": "SATA"}[v],
    "polegadas": lambda v: f"{v:g}".replace(".", ",") + " polegadas",
    "hz": lambda v: f"{v}Hz",
    "resolucao": lambda v: {"4k": "4K", "qhd": "QHD", "fhd": "Full HD", "hd": "HD"}[v],
    "watts": lambda v: f"{v}W",
    "selo80plus": lambda v: f"80 Plus {v.title()}",
    "mah": lambda v: f"{v}mAh",
    "litros": lambda v: f"{v:g}L".replace(".", ","),
    "btus": lambda v: f"{v} BTUs",
    "conexao": lambda v: "sem fio" if v == "sem_fio" else "",
    "vram": lambda v: f"{v}GB",
    "tipo_tela": lambda v: {"eletrica": "elétrica", "retratil": "retrátil", "tripe": "tripé", "tecido": ""}[v],
}


def extract_specs(title: str, names: list[str]) -> dict[str, Any]:
    t = normalize(title)
    out = {}
    for name in names:
        value = SPECS[name].extract(t)
        if value is not None:
            out[name] = value
    return out


# ----------------------------------------------------------------------
# Termos ("iphone 16", ["ps5", "playstation 5"])
# ----------------------------------------------------------------------
def _flat(text: str) -> str:
    return re.sub(r"[\s\-.]", "", text)


def has_term(norm_title: str, term: str | list[str]) -> bool:
    if isinstance(term, list):
        return any(has_term(norm_title, t) for t in term)
    term = normalize(str(term)).strip()  # YAML lê 27 / 23.8 como número
    if re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", norm_title):
        return True
    # "rtx5060" == "rtx 5060" == "rtx-5060" (sem aceitar "rtx 5060ti" pela fronteira)
    if " " in term or "-" in term:
        return re.search(r"(?<![a-z0-9])" + re.escape(_flat(term)) + r"(?![a-z0-9])",
                         _flat(norm_title)) is not None
    return False


# ----------------------------------------------------------------------
# Catálogo
# ----------------------------------------------------------------------
@dataclass
class Category:
    key: str
    nome: str
    detectar: list
    excluir: list = field(default_factory=list)
    comparar: str = "modelo"
    specs: list[str] = field(default_factory=list)
    busca: str | None = None
    mesma_marca: bool = False
    liquidez: str = "media"
    nota: str = ""
    # Segunda opinião paga: quando reaproveitar uma busca já feita
    #   especificacao -> mesma categoria + especificações (SSD NVMe 1TB de qualquer marca)
    #   modelo        -> mesmo modelo, qualquer marca de placa (RTX 5060 MSI = Gigabyte)
    #   marca_modelo  -> mesma marca + mesmo modelo (celular, fone, relógio)
    reuso: str | None = None
    validade_dias: int | None = None

    @property
    def reuse_mode(self) -> str:
        return self.reuso or ("especificacao" if self.comparar == "especificacao" else "marca_modelo")

    @property
    def reuse_days(self) -> int:
        return self.validade_dias or {"especificacao": 7, "modelo": 5}.get(self.reuse_mode, 3)


@dataclass
class TopProduct:
    nome: str
    categoria: str
    termos: list
    nao: list = field(default_factory=list)
    busca: str | None = None
    liquidez: str = "alta"


@dataclass
class Profile:
    """O que o catálogo sabe sobre uma oferta."""
    category: Category | None = None
    product: TopProduct | None = None
    specs: dict[str, Any] = field(default_factory=dict)
    brand: str | None = None
    query: str = ""

    @property
    def mode(self) -> str:
        return self.category.comparar if self.category else "modelo"

    @property
    def liquidez(self) -> str:
        if self.product:
            return self.product.liquidez
        return self.category.liquidez if self.category else "desconhecida"

    def as_dict(self) -> dict[str, Any]:
        return {
            "categoria_key": self.category.key if self.category else None,
            "categoria": self.category.nome if self.category else None,
            "produto_top": self.product.nome if self.product else None,
            "comparacao": self.mode,
            "liquidez": self.liquidez,
            "especificacoes": self.specs,
            "busca": self.query,
        }


class Catalog:
    def __init__(self, data: dict[str, Any]):
        self.brands = [normalize(b).strip() for b in data.get("marcas", [])]
        self.categories = {k: Category(key=k, **v) for k, v in (data.get("categorias") or {}).items()}
        self.products = [TopProduct(**p) for p in data.get("produtos_top") or []]
        for p in self.products:
            if p.categoria not in self.categories:
                raise ValueError(f"produto_top '{p.nome}': categoria '{p.categoria}' não existe")
        for c in self.categories.values():
            for s in c.specs:
                if s not in SPECS:
                    raise ValueError(f"categoria '{c.key}': especificação '{s}' não existe")

    @classmethod
    def load(cls, path: Path | str = CATALOG_PATH) -> "Catalog":
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {})

    # -- detecção -------------------------------------------------------
    BRAND_ALIASES = {"redmi": "xiaomi", "poco": "xiaomi", "mi": "xiaomi", "wd": "western digital",
                     "xpg": "adata", "walita": "philips", "thunderx3": "thunderx3", "tgt": "tgt"}

    def brand_of(self, title: str) -> str | None:
        """Marca que aparece PRIMEIRO no título ("Fone JBL ... para Samsung" -> jbl)."""
        t = normalize(title)
        best, pos = None, len(t) + 1
        for b in self.brands:
            m = re.search(r"(?<![a-z0-9])" + re.escape(b) + r"(?![a-z0-9])", t)
            if m and m.start() < pos:
                best, pos = b, m.start()
        return self.BRAND_ALIASES.get(best, best) if best else None

    def category_of(self, title: str) -> Category | None:
        for text in (head(title), title):  # nome primeiro: evita "compatível com SSD"
            t = normalize(text)
            for c in self.categories.values():
                if any(has_term(t, d) for d in c.detectar) and not any(has_term(t, x) for x in c.excluir):
                    return c
        return None

    def product_of(self, title: str) -> TopProduct | None:
        t = normalize(title)
        best = None
        for p in self.products:
            if any(has_term(t, x) for x in self.categories[p.categoria].excluir):
                continue  # "Capa para iPhone 16" não é iPhone 16
            if all(has_term(t, term) for term in p.termos) and not any(has_term(t, n) for n in p.nao):
                if best is None or len(p.termos) > len(best.termos):
                    best = p
        return best

    def profile(self, title: str) -> Profile:
        title = clean_title(title)
        product = self.product_of(title)
        category = self.categories[product.categoria] if product else self.category_of(title)
        specs = extract_specs(title, category.specs) if category else {}
        prof = Profile(category=category, product=product, specs=specs, brand=self.brand_of(title))
        prof.query = self.build_query(title, prof)
        return prof

    def build_query(self, title: str, prof: Profile) -> str:
        template = (prof.product.busca if prof.product else None) or \
                   (prof.category.busca if prof.category else None)
        if template:
            values = {k: SPEC_LABELS[k](v) for k, v in prof.specs.items()}
            values["marca"] = (prof.brand or "").title()
            query = re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), ""), template)
            if prof.category and prof.category.key == "tv":
                # O código escrito no post identifica a TV melhor que só marca + polegadas.
                from .matcher import model_codes, tokens
                codes = {code for code in model_codes(tokens(title)) if len(code) >= 5}
                original = re.findall(r"[\w.-]+", title)
                model = [word for word in original if tokens(word) & codes]
                query += " " + " ".join(model)
            return re.sub(r"\s+", " ", query).strip()
        from .pricing import build_query  # import tardio: pricing importa matcher
        return build_query(head(title))

    # -- equivalência ---------------------------------------------------
    def equivalent(self, prof: Profile, candidate_title: str) -> float:
        """Nota 0..1 para um candidato no modo especificação (0 = descartar)."""
        c = prof.category
        if c is None or self.category_of(candidate_title) is not c:
            return 0.0
        if prof.product and not self.matches_product(prof.product, candidate_title):
            return 0.0
        if c.mesma_marca and prof.brand and self.brand_of(candidate_title) != prof.brand:
            return 0.0
        cand = extract_specs(candidate_title, c.specs)
        confirmed = 0
        for name, value in prof.specs.items():
            if name in cand:
                if not SPECS[name].same(value, cand[name]):
                    return 0.0
                confirmed += 1
        missing = len(prof.specs) - confirmed
        if prof.specs and confirmed == 0:
            return 0.0  # nenhuma especificação confirmada: genérico demais
        return round(max(0.6, 0.95 - 0.1 * missing), 3)

    def reuse_key(self, prof: Profile, title: str) -> tuple[str, int]:
        """Chave para reaproveitar a busca paga + validade em dias.

        SSD NVMe 1TB Kingston e SSD NVMe 1TB WD -> mesma chave (marca não importa).
        RTX 5060 MSI e RTX 5060 Gigabyte       -> mesma chave (modelo).
        Galaxy A56 e Moto G85                  -> chaves diferentes (marca + modelo).
        """
        from .matcher import GENERIC_NAME_WORDS, model_codes, tokens

        c = prof.category
        mode = c.reuse_mode if c else "marca_modelo"
        days = c.reuse_days if c else 3
        specs = ",".join(f"{k}={v}" for k, v in sorted(prof.specs.items()))
        cat = c.key if c else "sem_categoria"
        if mode == "especificacao":
            brand = prof.brand if c and c.mesma_marca else ""
            return f"spec|{cat}|{brand}|{specs}", days
        if prof.product:
            ident = "top:" + prof.product.nome
        else:
            toks = tokens(head(title))
            codes = sorted(model_codes(toks))
            ident = " ".join(codes) or " ".join(sorted(t for t in toks if t not in GENERIC_NAME_WORDS))
        brand = (prof.brand or "") if mode == "marca_modelo" and not prof.product else ""
        return f"{mode}|{cat}|{brand}|{ident}|{specs}", days

    def excluded(self, category: Category, candidate_title: str, offer_title: str = "") -> bool:
        """Nome do candidato tem palavra de exclusão da categoria que a oferta não tem.

        Só o nome (antes da ficha técnica): "Ryzen 7 9800X3D, ... sem cooler" é processador.
        """
        t, o = normalize(head(candidate_title, 14)), normalize(offer_title)
        return any(has_term(t, x) and not has_term(o, x) for x in category.excluir)

    def spec_ok(self, prof: Profile, name: str, candidate_title: str) -> bool:
        """Especificação compatível (ou ausente no candidato)."""
        value = SPECS[name].extract(normalize(candidate_title))
        return value is None or SPECS[name].same(prof.specs[name], value)

    def matches_product(self, product: TopProduct, candidate_title: str) -> bool:
        t = normalize(candidate_title)
        if any(has_term(t, x) for x in self.categories[product.categoria].excluir):
            return False
        return all(has_term(t, term) for term in product.termos) and not any(has_term(t, n) for n in product.nao)


_CATALOG: Catalog | None = None


def get_catalog() -> Catalog:
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = Catalog.load() if CATALOG_PATH.exists() else Catalog({})
    return _CATALOG
