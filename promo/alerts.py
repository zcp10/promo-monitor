"""Envia alertas pelo seu bot do Telegram (Bot API).

O bot só fala com VOCÊ: crie com o @BotFather, mande /start para ele e use
o seu chat_id em TG_ALERT_CHAT_ID. O bot não entra em grupo nenhum.
"""
from __future__ import annotations

import html
import logging

import httpx

from .models import Analysis

log = logging.getLogger(__name__)

CHANNEL_NAMES = {
    "mercadolivre_classico": "Mercado Livre (Clássico)",
    "mercadolivre_premium": "Mercado Livre (Premium)",
    "shopee": "Shopee",
    "venda_direta": "Venda direta (OLX/WhatsApp)",
}


def brl(value: float | None) -> str:
    if value is None:
        return "—"
    s = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {s}"


def resale_search_links(query: str) -> dict[str, str]:
    """Busca pronta nos canais de revenda (abre no navegador/app da pessoa, sem robô)."""
    from urllib.parse import quote

    q = quote(query.strip())
    return {
        "OLX": f"https://www.olx.com.br/brasil?q={q}",
        "Marketplace": f"https://www.facebook.com/marketplace/search/?query={q}",
    }


def format_opportunity(a: Analysis) -> str:
    o, b = a.offer, a.best
    e = html.escape
    header = ("⚠️ <b>VERIFICAR: PREÇO MUITO ABAIXO DO MERCADO</b>\n<i>Pode ser bug de preço ou "
              "comparação com produto diferente. Confira antes de comprar.</i>"
              if (a.profile or {}).get("verificar") else "🟢 <b>OPORTUNIDADE DE REVENDA</b>")
    lines = [
        header,
        "",
        f"<b>{e(o.title)}</b>",
        f"💰 Compra: <b>{brl(o.price)}</b>" + (f" (era {brl(o.old_price)})" if o.old_price else ""),
    ]
    qty = (a.profile or {}).get("quantidade")
    if qty:
        lines.append(f"📦 {qty} unidades → {brl(o.price / qty)} cada (análise por unidade)")
    if o.coupon:
        lines.append(f"🎟️ Cupom: <code>{e(o.coupon)}</code>")
    if o.store:
        lines.append(f"🏬 Loja: {e(o.store)}")
    p = a.profile or {}
    if p.get("categoria"):
        tag = f"⭐ Top revenda: {e(p['produto_top'])}" if p.get("produto_top") else f"📦 {e(p['categoria'])}"
        lines.append(f"{tag} · liquidez {e(str(p.get('liquidez')))}")
    lines += [
        "",
        f"📊 Mercado: mediana {brl(a.market_median)} · menor {brl(a.market_min)} "
        f"({len(a.accepted_quotes)} preços, confiança {a.confidence})",
    ]
    if p.get("segunda_opiniao"):
        lines.append(f"<i>2ª opinião (Google Shopping): {e(str(p['segunda_opiniao']))}</i>")
    if p.get("equivalentes"):
        lines.append(f"<i>Inclui {p['equivalentes']} preço(s) de produto equivalente (outra marca/modelo).</i>")
    if b:
        lines += [
            f"🏷️ Melhor canal: <b>{CHANNEL_NAMES.get(b.channel, b.channel)}</b> vendendo a {brl(b.sale_price)}",
            f"💵 Lucro líquido estimado: <b>{brl(b.profit)}</b>" + (" por unidade" if qty else "")
            + f" (ROI {b.roi:.0%})" + (f" · <b>{brl(p['lucro_total'])}</b> no total" if qty else ""),
            f"<i>Taxas {brl(b.commission + b.fixed_fee)} · frete {brl(b.shipping)} · "
            f"imposto {brl(b.tax)} · embalagem {brl(b.packaging)} · reserva {brl(b.risk)}</i>",
        ]
    if o.chat_title:
        lines.append(f"📣 Fonte: {e(o.chat_title)}")
    if o.url:
        lines.append(f'🔗 <a href="{e(o.url, quote=True)}">Abrir oferta</a>')
    links = resale_search_links(p.get("busca") or o.title)
    lines.append("🔎 Preços de revenda: " + " · ".join(
        f'<a href="{e(url, quote=True)}">{name}</a>' for name, url in links.items()))
    return "\n".join(lines)


def format_watch_alert(name: str, price: float, target: float | None, store: str | None,
                       url: str | None) -> str:
    e = html.escape
    lines = [
        "🔔 <b>PREÇO-ALVO ATINGIDO</b>",
        f"<b>{e(name)}</b>",
        f"Agora: <b>{brl(price)}</b>" + (f" · alvo {brl(target)}" if target else ""),
    ]
    if store:
        lines.append(f"Loja: {e(store)}")
    if url:
        lines.append(f'<a href="{e(url, quote=True)}">Abrir produto</a>')
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str | None, chat_id: str | None):
        self.token, self.chat_id = bot_token, chat_id

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, text: str) -> bool:
        if not self.enabled:
            log.info("Alerta (bot não configurado):\n%s", text)
            return False
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                      "disable_web_page_preview": False},
            )
        if r.status_code != 200:
            log.error("Falha ao enviar alerta: %s %s", r.status_code, r.text[:200])
            return False
        return True
