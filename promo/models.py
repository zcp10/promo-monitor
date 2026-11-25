"""Estruturas de dados compartilhadas."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Offer:
    """Uma oferta encontrada (post do Telegram, watchlist ou entrada manual)."""
    title: str
    price: float
    source: str = "telegram"
    old_price: float | None = None
    coupon: str | None = None
    store: str | None = None
    url: str | None = None
    shipping_cost: float | None = None
    raw_text: str | None = None
    chat_id: int | None = None
    chat_title: str | None = None
    message_id: int | None = None
    posted_at: datetime | None = None
    quantity: int = 1          # unidades vendidas juntas ("5 fones por R$ 50" -> 5)
    id: str | None = None

    @property
    def unit_price(self) -> float:
        return self.price / max(1, self.quantity)

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row.pop("id")
        if self.posted_at:
            row["posted_at"] = self.posted_at.isoformat()
        return row


@dataclass
class Quote:
    """Um preço de mercado encontrado para comparação."""
    provider: str
    title: str
    price: float
    store: str | None = None
    shipping: float | None = None
    url: str | None = None
    match_score: float = 0.0
    accepted: bool = True
    equivalent: bool = False   # aceito por especificação (outro modelo/marca equivalente)
    condition: str | None = None  # new/used quando a fonte informa explicitamente


@dataclass
class ChannelResult:
    channel: str
    sale_price: float
    commission: float
    fixed_fee: float
    shipping: float
    tax: float
    packaging: float
    risk: float
    net_revenue: float
    profit: float
    roi: float


@dataclass
class Analysis:
    offer: Offer
    quotes: list[Quote] = field(default_factory=list)
    market_median: float | None = None
    market_min: float | None = None
    total_cost: float = 0.0
    best: ChannelResult | None = None
    channels: list[ChannelResult] = field(default_factory=list)
    is_profitable: bool = False
    confidence: str = "baixa"
    reasons: list[str] = field(default_factory=list)
    profile: dict[str, Any] = field(default_factory=dict)  # categoria, produto top, liquidez...

    @property
    def accepted_quotes(self) -> list[Quote]:
        return [q for q in self.quotes if q.accepted]
