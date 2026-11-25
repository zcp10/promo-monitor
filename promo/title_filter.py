"""Limpeza de chamadas publicitárias, sem remover termos técnicos do produto."""
from __future__ import annotations
import re
import unicodedata

DEFAULT_IGNORED_HEADLINES = (
    "combina tudo", "combina com tudo", "esse combina com tudo", "essa combina com tudo",
    "super promoção", "promoção imperdível", "várias opções de cores", "o tênis coringa",
    "resgate hoje às 19:00 usar amanhã",
)
COUPON_INSTRUCTION = re.compile(
    r"^(?:resgate|resgatar|ative|ativar|pegue|libere)"
    r"(?:\s+(?:o|a|seu|sua|cupom|cupons|hoje|amanha|as|a|partir|das|de|ate|h|horas|"
    r"para|pra|usar|use|utilizar|utilize|somente|apenas|valido|valida|dia|agora|\d+))*$")
PREFIX = re.compile(
    r"^(?:baixou(?:\s+de\s+pre[çc]o)?|voltou|barateou|super\s+promo[çc][aã]o|"
    r"promo[çc][aã]o(?:\s+imperd[ií]vel)?|oferta\s+do\s+dia|corre|corra|aproveite|"
    r"olha\s+(?:o\s+)?que\s+eu\s+encontrei|pre[çc][aã]o)"
    r"\b[\s!‼:;,.—–-]*(?:(?:a|o|as|os)\s+)?", re.IGNORECASE)


def _key(text: str) -> str:
    text=unicodedata.normalize("NFKD",text.lower())
    text="".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"[a-z0-9]+",text))


def clean_title(title: str, rules: dict | None = None) -> str:
    """Ignora frases completas; 'combina com tudo' em uma descrição não bloqueia o produto."""
    title=re.sub(r"\s+"," ",title or "").strip(" -–—:!‼")
    excluded={_key(x) for x in DEFAULT_IGNORED_HEADLINES}
    excluded.update(_key(x) for x in (rules or {}).get("ignored_headlines",[]) if isinstance(x,str))
    if _key(title) in excluded or COUPON_INSTRUCTION.fullmatch(_key(title)):return ""
    while True:
        cleaned=PREFIX.sub("",title,count=1).strip(" -–—:!‼")
        if cleaned==title:break
        title=cleaned
    return "" if _key(title) in excluded else title
