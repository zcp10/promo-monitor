"""Calculadora de revenda.

Lógica (por canal de venda):
    preço_venda   = mediana_mercado × (1 − desconto_para_vender_rápido) × fator_do_canal
    receita_líq.  = preço_venda − comissão − taxa_fixa − frete_pago_por_você
                    − imposto − embalagem − rateio_DAS − reserva_de_risco
    custo_total   = preço_da_oferta + frete_da_compra
    lucro         = receita_líq. − custo_total
    ROI           = lucro ÷ custo_total

É rentável quando: há cotações suficientes, lucro ≥ mínimo e ROI ≥ mínimo.
"""
from __future__ import annotations

import statistics

from .config import Settings
from .models import Analysis, ChannelResult, Offer, Quote


def _fixed_fee(bands: list[dict], sale_price: float) -> float:
    for band in sorted(bands or [], key=lambda b: b["below"]):
        if sale_price < float(band["below"]):
            return float(band["fee"])
    return 0.0


def channel_result(name: str, cfg: dict, settings: Settings, median: float,
                   total_cost: float) -> ChannelResult:
    rules, costs = settings.get("rules", {}), settings.get("costs", {})
    sale = median * (1 - float(rules.get("sale_discount_vs_median", 0.0)))
    sale *= float(cfg.get("price_factor", 1.0))
    sale = round(sale, 2)

    commission = sale * float(cfg.get("commission", 0.0))
    fixed_fee = _fixed_fee(cfg.get("fixed_fee_bands", []), sale)
    free_from = float(cfg.get("free_shipping_from", 0) or 0)
    shipping = float(cfg.get("shipping_seller_share", 0.0)) if sale >= free_from else 0.0
    tax = sale * float(costs.get("tax_rate", 0.0))
    das = float(costs.get("das_monthly", 0.0)) / max(1, int(costs.get("monthly_sales_estimate", 1)))
    packaging = float(costs.get("packaging", 0.0)) + das
    risk = sale * float(rules.get("risk_buffer", 0.0))

    net = sale - commission - fixed_fee - shipping - tax - packaging - risk
    profit = net - total_cost
    roi = profit / total_cost if total_cost > 0 else 0.0
    return ChannelResult(
        channel=name, sale_price=sale, commission=round(commission, 2),
        fixed_fee=round(fixed_fee, 2), shipping=round(shipping, 2), tax=round(tax, 2),
        packaging=round(packaging, 2), risk=round(risk, 2), net_revenue=round(net, 2),
        profit=round(profit, 2), roi=round(roi, 4),
    )


def breakeven_price(cfg: dict, settings: Settings, total_cost: float) -> float:
    """Menor preço de venda que empata (lucro zero) nesse canal."""
    rules, costs = settings.get("rules", {}), settings.get("costs", {})
    pct = float(cfg.get("commission", 0)) + float(costs.get("tax_rate", 0)) + float(rules.get("risk_buffer", 0))
    das = float(costs.get("das_monthly", 0.0)) / max(1, int(costs.get("monthly_sales_estimate", 1)))
    fixed = float(costs.get("packaging", 0.0)) + das
    guess = total_cost
    for _ in range(5):  # taxa fixa e frete dependem do próprio preço: converge em poucas voltas
        fee = _fixed_fee(cfg.get("fixed_fee_bands", []), guess)
        free_from = float(cfg.get("free_shipping_from", 0) or 0)
        ship = float(cfg.get("shipping_seller_share", 0.0)) if guess >= free_from else 0.0
        guess = (total_cost + fixed + fee + ship) / max(0.01, 1 - pct)
    return round(guess, 2)


def min_profit_for(unit_cost: float, rules: dict) -> float:
    """Lucro mínimo por unidade: base fixa + % do custo (ex.: R$ 25 + 5%).

    R$ 10 -> R$ 25,50 · R$ 100 -> R$ 30 · R$ 1.000 -> R$ 75 · R$ 3.000 -> R$ 175.
    """
    base = float(rules.get("min_profit_base", rules.get("min_profit", 0)) or 0)
    pct = float(rules.get("min_profit_pct", 0) or 0)
    return round(base + pct * unit_cost, 2)


def confidence_level(prices: list[float], min_quotes: int) -> str:
    if len(prices) < min_quotes:
        return "baixa"
    if len(prices) >= 6:
        q = statistics.quantiles(prices, n=4)
        spread = (q[2] - q[0]) / statistics.median(prices)
        if spread <= 0.25:
            return "alta"
    return "media"


def analyze(offer: Offer, quotes: list[Quote], settings: Settings,
            profile: dict | None = None) -> Analysis:
    rules = dict(settings.get("rules", {}) or {})
    costs = settings.get("costs", {})
    analysis = Analysis(offer=offer, quotes=quotes, profile=profile or {})
    # Regras por liquidez (ex.: produto que sai rápido aceita ROI menor)
    liquidez = analysis.profile.get("liquidez")
    rules.update((rules.get("por_liquidez") or {}).get(liquidez) or {})
    # Grupo criado no painel (ex.: "Fones e carregadores") vence a liquidez e a regra global
    group = analysis.profile.get("grupo") or {}
    rules.update({k: group[k] for k in ("min_profit_base", "min_profit_pct", "min_roi")
                  if group.get(k) is not None})

    inbound = offer.shipping_cost if offer.shipping_cost is not None else float(
        costs.get("inbound_shipping_default", 0.0))
    # Tudo por UNIDADE: "5 fones por R$ 50" -> custo de R$ 10 cada, vendidos um a um
    qty = max(1, int(getattr(offer, "quantity", 1) or 1))
    analysis.total_cost = round((offer.price + inbound) / qty, 2)
    if qty > 1:
        analysis.profile["quantidade"] = qty

    prices = sorted(q.price for q in analysis.accepted_quotes)
    min_quotes = int(rules.get("min_quotes", 3))
    if not prices:
        analysis.reasons.append("Nenhum preço de mercado compatível encontrado.")
        return analysis

    analysis.market_median = round(statistics.median(prices), 2)
    analysis.market_min = round(prices[0], 2)
    analysis.confidence = confidence_level(prices, min_quotes)
    n_equiv = sum(q.equivalent for q in analysis.accepted_quotes)
    if n_equiv:
        analysis.profile["equivalentes"] = n_equiv
        if analysis.confidence == "alta":
            analysis.confidence = "media"  # preço de produtos equivalentes, não do mesmo modelo
        if n_equiv * 2 >= len(prices) and len(prices) >= 4:
            # Equivalentes vão do básico ao topo de linha: quem compra o "genérico"
            # paga perto do quartil inferior, não da mediana.
            analysis.market_median = round(statistics.quantiles(prices, n=4)[0], 2)
            analysis.profile["referencia"] = "quartil inferior (maioria equivalentes)"
    # O comprador escolhe o anúncio mais barato do MESMO modelo: vender muito acima
    # dele não acontece (vendedores de marketplace inflam a mediana).
    same_model = sorted(q.price for q in analysis.accepted_quotes if not q.equivalent)
    if same_model and analysis.market_median > 1.10 * same_model[0]:
        analysis.market_median = round(1.10 * same_model[0], 2)
        analysis.profile["referencia"] = "menor preço do mesmo modelo + 10%"
    # Oferta custando menos de 40% do mercado: bug de preço OU comparação errada
    if analysis.market_median and offer.unit_price < 0.4 * analysis.market_median:
        analysis.profile["verificar"] = True
        analysis.confidence = "baixa"

    for name, cfg in (settings.get("channels", {}) or {}).items():
        if cfg.get("enabled", True):
            analysis.channels.append(
                channel_result(name, cfg, settings, analysis.market_median, analysis.total_cost))
    if not analysis.channels:
        analysis.reasons.append("Nenhum canal de venda habilitado.")
        return analysis

    analysis.best = max(analysis.channels, key=lambda c: c.profit)
    best = analysis.best
    min_profit = min_profit_for(analysis.total_cost, rules)
    min_roi = float(rules.get("min_roi", 0))
    if qty > 1:
        analysis.profile["lucro_total"] = round(best.profit * qty, 2)

    if len(prices) < min_quotes:
        analysis.reasons.append(f"Só {len(prices)} preço(s) comparável(is); mínimo é {min_quotes}.")
    if best.profit < min_profit:
        analysis.reasons.append(f"Lucro R$ {best.profit:.2f} abaixo do mínimo R$ {min_profit:.2f}.")
    if best.roi < min_roi:
        analysis.reasons.append(f"ROI {best.roi:.0%} abaixo do mínimo {min_roi:.0%}.")
    if offer.unit_price >= analysis.market_min:
        analysis.reasons.append("Já existe oferta igual ou mais barata no mercado.")

    if analysis.profile.get("verificar"):
        analysis.reasons.append("Preço abaixo de 40% do mercado: provável golpe, assinatura, erro de preço "
                                "ou comparação com outro produto. Confira manualmente.")
    analysis.is_profitable = (len(prices) >= min_quotes and best.profit >= min_profit
                              and best.roi >= min_roi and not analysis.profile.get("verificar"))
    return analysis
