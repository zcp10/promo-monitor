"""Orquestra: post → oferta → preços de mercado → análise → banco → alerta."""
from __future__ import annotations

import copy
import hashlib
import logging
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from .alerts import TelegramNotifier, format_opportunity, format_watch_alert
from .browser_shopping import GoogleBrowserSource
from .catalog import get_catalog
from .config import Settings
from .matcher import filter_quotes, tokens
from .models import Analysis, Offer, Quote
from .parser import AIParser, extract_quantity, is_promotional_only_post, parse_post, strip_quantity
from .title_filter import clean_title
from .pricing import PageScraper, build_sources, collect_quotes
from .profit import analyze
from .reef import ReefSource
from .storage import Repository

log = logging.getLogger(__name__)
BRT = timezone(timedelta(hours=-3))  # Brasília (sem horário de verão desde 2019)


class Pipeline:
    def __init__(self, settings: Settings, repo: Repository, notifier: TelegramNotifier):
        self.settings, self.repo, self.notifier = settings, repo, notifier
        self.sources, self.reserve = build_sources(settings)
        reef_cfg = settings.get("pricing.reef", {}) or {}
        self.reef_source = (ReefSource(settings.secrets.reef_api_key, repo,
            reef_cfg.get("site", "buscape"), int(reef_cfg.get("max_results", 30)),
            accept_used=bool(reef_cfg.get("accept_used", False)))
            if reef_cfg.get("enabled") and settings.secrets.reef_api_key else None)
        browser_cfg = settings.get("pricing.google_browser", {}) or {}
        self.browser_source = GoogleBrowserSource(browser_cfg) if browser_cfg.get("enabled", False) else None
        log.info("Preços em sequência: referência salva → KaBuM%s%s%s (pagas só p/ categorias do catálogo; "
                 "'sem resultado' guardado %sh).", " → Chrome" if self.browser_source else "",
                 " → HasData" if self.reserve else "", " → ReefAPI" if self.reef_source else "",
                 settings.get("pricing.no_result_cache_hours", 24))
        self.catalog = get_catalog()
        s = settings.secrets
        ai_cfg = settings.get("ai_parser", {}) or {}
        self.ai = (AIParser(s.anthropic_api_key, ai_cfg.get("model", "claude-haiku-5-5"))
                   if ai_cfg.get("enabled") and s.anthropic_api_key else None)
        self.ignore = [w.lower() for w in settings.get("telegram.ignore_keywords", []) or []]
        self.title_rules = settings.get("title_filter", {}) or {}
        self._yaml_title_rules = copy.deepcopy(self.title_rules)
        self._recent: dict[tuple, float] = {}
        # Regras: settings.yaml é a base; o painel (app_settings/rule_groups) sobrepõe.
        self._yaml_rules = copy.deepcopy(settings.get("rules", {}) or {})
        self.groups: dict[str, dict] = {}   # categoria -> grupo do usuário
        self._rules_at = 0.0
        self._categories_synced = False
        self._apply_search_rules()

    async def refresh_rules(self, force: bool = False) -> None:
        """Relê regras e grupos do painel no máximo a cada 5 min (falha = mantém as atuais)."""
        if not force and time.monotonic() - self._rules_at < 300:
            return
        self._rules_at = time.monotonic()
        try:
            if not self._categories_synced:
                await self.repo.sync_categories([
                    {"key": c.key, "nome": c.nome, "liquidez": c.liquidez, "comparar": c.comparar}
                    for c in self.catalog.categories.values()])
                self._categories_synced = True
            remote, groups = await self.repo.remote_rules()
        except Exception as exc:
            log.warning("Não consegui ler as regras do painel (%s); usando as atuais.", exc)
            return
        rules = copy.deepcopy(self._yaml_rules)
        for key, value in (remote or {}).items():
            if key == "por_liquidez" and isinstance(value, dict):
                merged = rules.setdefault("por_liquidez", {})
                for liq, over in value.items():
                    merged[liq] = {**(merged.get(liq) or {}), **(over or {})}
            elif value is not None:
                rules[key] = value
        self.settings.raw["rules"] = rules
        self._apply_search_rules()
        self.groups = {}
        for g in groups:
            for cat in g.get("categories") or []:
                self.groups.setdefault(cat, g)  # o grupo criado primeiro vence

    def _apply_search_rules(self):
        """Google and title filters are editable JSON rules in app_settings."""
        search = self.settings.get("rules.google_search", {}) or {}
        self.title_rules = copy.deepcopy(self._yaml_title_rules)
        self.title_rules["ignored_headlines"] = list(self.title_rules.get("ignored_headlines", [])) + [
            x for x in search.get("ignored_headlines", []) if isinstance(x, str)]
        if self.browser_source and hasattr(self.browser_source, "config"):
            for key, floor, ceiling, default in (("hourly_limit", 1, 60, 10), ("interval_seconds", 60, 3600, 60)):
                try:
                    value = int(search.get(key, self.browser_source.config.get(key, default)))
                except (TypeError, ValueError):
                    value = default
                self.browser_source.config[key] = max(floor, min(ceiling, value))

    def _google_allowed(self, profile):
        search = self.settings.get("rules.google_search", {}) or {}
        key = profile.category.key if profile.category else None
        allowed = search.get("allowed_categories", [])
        return not ((search.get("catalog_only", False) and key is None) or
                    (isinstance(allowed, list) and allowed and key not in allowed))

    def extract(self, text: str) -> Offer | None:
        low = (text or "").lower()
        if any(w in low for w in self.ignore):
            return None
        offer = parse_post(text, title_rules=self.title_rules)
        if offer is None and is_promotional_only_post(text, self.title_rules):
            log.info("Post ignorado: chamada promocional ou cupom sem produto identificável.")
            return None
        if offer is None and self.ai is not None:
            try:
                offer = self.ai.parse(text)
            except Exception as exc:  # IA é plano B: nunca derruba o monitor
                log.warning("Extração com IA falhou: %s", exc)
        if offer is not None:
            offer.title = clean_title(offer.title, self.title_rules)
            if not offer.title:
                return None
        return offer

    def _filter(self, title: str, quotes, profile, offer_price=None, offer_store=None):
        pricing = self.settings.get("pricing", {}) or {}
        filter_quotes(title, quotes, exclude_words=pricing.get("exclude_words", []),
                      min_score=float(pricing.get("min_match_score", 0.6)), offer_price=offer_price,
                      exclude_stores=pricing.get("exclude_stores", []), offer_store=offer_store,
                      profile=profile, catalog=self.catalog,
                      accept_used=bool(self.settings.get("pricing.reef.accept_used", False)))
        return quotes

    @staticmethod
    def _per_unit(quotes):
        """Anúncio de loja com várias unidades ("Kit 2 fones") vira preço por unidade."""
        for q in quotes:
            n = extract_quantity(q.title)
            if n > 1:
                q.price = round(q.price / n, 2)
                q.title = f"{q.title} [÷{n}]"
        return quotes

    async def market_quotes(self, title: str, profile, offer_price=None, offer_store=None):
        """KaBuM primeiro; Shopping só se não houver preço compatível na KaBuM."""
        quotes, _ = await self._market_quotes_with_status(title, profile, offer_price, offer_store)
        return quotes

    async def _market_quotes_with_status(self, title: str, profile, offer_price=None, offer_store=None):
        """Reusa cotações por 30 dias, sem renovar a data ao ler o cache."""
        cleaned = clean_title(title, self.title_rules)
        if not cleaned:
            return [], "ignorado_chamada_promocional"
        if cleaned != title:
            title, profile = cleaned, self.catalog.profile(cleaned)
        days = float(self.settings.get("pricing.reference_cache_days", 30))
        # Mesma consulta gera candidatos; o filtro confere novamente o modelo/variante
        # e a loja/preço da promoção atual antes de aceitar a referência salva.
        query = " ".join(profile.query.casefold().split())
        key = "reference:v1:" + hashlib.sha256(query.encode("utf-8")).hexdigest()
        if days > 0:
            try:
                hit = await self.repo.get_cache(key)
                if hit:
                    when = datetime.fromisoformat(str(hit["created_at"]).replace("Z", "+00:00"))
                    age = datetime.now(timezone.utc) - when
                    if timedelta(0) <= age < timedelta(days=days):
                        quotes = [Quote(**q) for q in hit["quotes"]]
                        self._filter(title, quotes, profile, offer_price, offer_store)
                        if any(q.accepted for q in quotes):
                            log.info("Referência de mercado reaproveitada de %s | busca: %s", when.isoformat(), profile.query)
                            return quotes, "referencia_salva:" + when.isoformat()
            except Exception as exc:
                log.warning("Cache de referência indisponível; consultando fontes: %s", exc)
        quotes, status = await self._fetch_market_quotes(title, profile, offer_price, offer_store)
        # Uma resposta reaproveitada pelo navegador já tem sua própria data original;
        # não inicia outro prazo de 30 dias sobre cotações antigas.
        if days > 0 and status != "cached" and any(q.accepted for q in quotes):
            try:
                await self.repo.put_cache(key, profile.query, "market_reference", [asdict(q) for q in quotes])
            except Exception as exc:
                log.warning("Não consegui salvar a referência de mercado: %s", exc)
        return quotes, status

    async def _fetch_market_quotes(self, title: str, profile, offer_price=None, offer_store=None):
        cleaned = clean_title(title, self.title_rules)
        if not cleaned:
            return [], "ignorado_chamada_promocional"
        if cleaned != title:
            title, profile = cleaned, self.catalog.profile(cleaned)
        quotes = self._per_unit(await collect_quotes(self.sources, profile.query))
        self._filter(title, quotes, profile, offer_price, offer_store)
        if any(q.accepted and q.provider == "kabum" for q in quotes):
            return quotes, "dispensado_kabum"
        status = None
        paid = list(self.reserve) + ([self.reef_source] if self.reef_source else [])
        stages = ([self.browser_source] if self.browser_source else []) + paid
        if self.browser_source and not self._google_allowed(profile):
            stages = [s for s in stages if s is not self.browser_source]
            status = "google_fora_das_categorias_configuradas"
            log.info("Google pulado pelas categorias configuradas | busca: %s", profile.query)
        # Crédito pago só para o que o radar sabe revender (fone, celular, placa...):
        # bermuda, perfume e brinquedo nunca gastam HasData/Reef.
        skip_reason = None
        if profile.category is None:
            stages = [s for s in stages if s not in paid]
            status = skip_reason = "sem_categoria_sem_busca_paga"
        # "Nada encontrado" também fica guardado: a mesma oferta repostada em 5 grupos
        # não paga 5 vezes pela mesma resposta vazia.
        negative_key = "no-result:v1:" + hashlib.sha256(" ".join(profile.query.casefold().split()).encode()).hexdigest()
        if any(s in paid for s in stages) and await self._recent_no_result(negative_key):
            stages = [s for s in stages if s not in paid]
            status = skip_reason = "sem_resultado_recente"
            log.info("Busca paga pulada: '%s' já voltou sem preço compatível nas últimas %sh.",
                     profile.query, self.settings.get("pricing.no_result_cache_hours", 24))
        searched_paid = False
        for source in stages:
            if source in paid:
                provider, rule, default = (("hasdata_attempt", "segunda_opiniao_limite_diario", 3)
                                           if source in self.reserve else ("reef_usage", "reef_limite_diario", 11))
                if not await self._paid_quota_left(provider, rule, default):
                    status = f"{source.name}_limite_diario"
                    log.info("%s: limite diário atingido; seguindo para a próxima fonte.", source.name)
                    continue
                if source in self.reserve:  # HasData conta a tentativa antes (falha também gasta)
                    await self._record_paid(provider, profile.query, {})
            extra = self._per_unit(await collect_quotes([source], profile.query))
            self._filter(title, extra, profile, offer_price, offer_store)
            status = getattr(source, "last_status", "ok" if extra else "sem_resultado")
            if source is self.reef_source:  # crédito real cobrado pela Reef nesta consulta
                await self._record_paid("reef_usage", profile.query,
                                        {"credits": getattr(source, "charged_credits", 0), "status": status})
            if source in paid:
                searched_paid = True
            log.info("Fonte %s: %s | compatíveis: %s | busca: %s", source.name, status,
                     sum(q.accepted for q in extra), profile.query)
            quotes += extra
            if any(q.accepted for q in extra):
                return quotes, status if source is self.browser_source else "fonte:" + source.name
        if searched_paid:  # pagou e não achou nada compatível: não repetir por 24 h
            try:
                await self.repo.put_cache(negative_key, profile.query, "no_result", [])
            except Exception as exc:
                log.warning("Não consegui salvar 'sem resultado': %s", exc)
        return quotes, skip_reason or status

    async def _recent_no_result(self, key: str) -> bool:
        hours = float(self.settings.get("pricing.no_result_cache_hours", 24) or 0)
        if hours <= 0:
            return False
        try:
            hit = await self.repo.get_cache(key)
        except Exception:
            return False
        if not hit:
            return False
        when = datetime.fromisoformat(str(hit["created_at"]).replace("Z", "+00:00"))
        return datetime.now(timezone.utc) - when < timedelta(hours=hours)

    async def _paid_quota_left(self, provider: str, rule: str, default: int) -> bool:
        """Teto diário de uma fonte paga (editável no painel); falha ao conferir = não gasta."""
        limit = int(self.settings.get(f"rules.{rule}", default) or 0)
        if limit <= 0:
            return False
        today = datetime.now(BRT).replace(hour=0, minute=0, second=0, microsecond=0)
        try:
            used = await self.repo.paid_searches_since(provider, today.astimezone(timezone.utc).isoformat())
        except Exception as exc:
            log.warning("Não consegui conferir a cota de %s; pulando: %s", provider, exc)
            return False
        return used < limit

    async def _record_paid(self, provider: str, query: str, info: dict) -> None:
        import uuid
        try:
            await self.repo.put_cache(f"{provider}:" + uuid.uuid4().hex, query, provider, [info] if info else [])
        except Exception as exc:
            log.warning("Não consegui registrar o uso de %s: %s", provider, exc)

    async def evaluate(self, offer: Offer, quotes=None) -> Analysis:
        if self._above_price_limit(offer):
            return Analysis(offer=offer, total_cost=offer.unit_price,
                            reasons=["Preço da promoção por unidade acima do limite de compra."],
                            profile={"limite_preco": True})
        # Analisa UMA unidade: "Kit 5 fones por R$ 50" compara o preço de 1 fone (R$ 10)
        unit_title = strip_quantity(offer.title) if offer.quantity > 1 else offer.title
        unit_title = clean_title(unit_title, self.title_rules)
        profile = self.catalog.profile(unit_title)
        fresh = quotes is None
        browser_status = None
        if fresh:
            quotes, browser_status = await self._market_quotes_with_status(
                unit_title, profile, offer.unit_price, offer.store)
        else:
            self._filter(unit_title, quotes, profile, offer.unit_price, offer.store)
        info = profile.as_dict()
        group = self.groups.get(info.get("categoria_key") or "")
        if group:
            info["grupo"] = {k: group.get(k) for k in ("name", "min_profit_base", "min_profit_pct", "min_roi")}
        analysis = analyze(offer, quotes, self.settings, copy.deepcopy(info))
        if any(q.condition for q in quotes):
            analysis.profile["condicoes_referencia"] = {
                "usado": sum(q.accepted and q.condition == "used" for q in quotes),
                "novo": sum(q.accepted and q.condition in ("new", "novo") for q in quotes)}
        if browser_status and browser_status.startswith("referencia_salva:"):
            analysis.profile["referencia_mercado_coletada_em"] = browser_status.split(":", 1)[1]
        elif browser_status and browser_status.startswith("fonte:"):
            analysis.profile["fonte_mercado"] = browser_status.split(":", 1)[1]
        elif browser_status:
            analysis.profile["google_browser"] = browser_status
        return analysis

    def _above_price_limit(self, offer: Offer) -> bool:
        limit = float(self.settings.get("rules.max_offer_price", 0) or 0)
        return limit > 0 and round(offer.unit_price, 2) > limit

    async def handle_offer(self, offer: Offer, quotes=None) -> Analysis | None:
        await self.refresh_rules()
        if self._above_price_limit(offer):
            log.info("Ignorado (limite de compra R$ %.2f/un): %s | R$ %.2f/un",
                     float(self.settings.get("rules.max_offer_price")), offer.title, offer.unit_price)
            return None
        min_price = float(self.settings.get("rules.min_offer_price", 0) or 0)
        if offer.price < min_price:
            log.debug("Ignorado (preço baixo): %s", offer.title)
            return None
        # A mesma promoção costuma ser repostada em vários grupos: analisa uma vez
        # a cada 6 h (economiza créditos de busca e evita alerta repetido).
        if offer.source == "telegram":
            key = (" ".join(sorted(tokens(offer.title))), round(offer.price))
            now = time.monotonic()
            self._recent = {k: t for k, t in self._recent.items() if now - t < 6 * 3600}
            if key in self._recent:
                log.debug("Repostagem ignorada: %s", offer.title)
                return None
            self._recent[key] = now
        offer_id = await self.repo.save_offer(offer)
        if offer_id is None:
            return None  # já processado
        analysis = await self.evaluate(offer, quotes)
        analysis_id = await self.repo.save_analysis(analysis, offer_id)
        best = analysis.best
        log.info("%s | %s | lucro %s | %s", "✅" if analysis.is_profitable else "·",
                 offer.title[:60], f"{best.profit:.2f}" if best else "—", "; ".join(analysis.reasons))
        if analysis.is_profitable:
            text = format_opportunity(analysis)
            if await self.notifier.send(text):
                await self.repo.save_alert(text, analysis_id=analysis_id)
        return analysis

    async def handle_text(self, text: str, **meta) -> Analysis | None:
        if await self.repo.seen(meta.get("chat_id"), meta.get("message_id")):
            return None
        offer = self.extract(text)
        if offer is None:
            return None
        for key, value in meta.items():
            setattr(offer, key, value)
        return await self.handle_offer(offer)

    async def check_watchlist(self) -> None:
        """Checa cada produto fixo: preço atual (URL exata ou menor preço compatível)."""
        scraper = PageScraper(self.settings.secrets.hasdata_api_key) \
            if self.settings.secrets.hasdata_api_key else None
        import httpx

        for item in await self.repo.list_watchlist():
            try:
                quote, quotes = None, None
                if item.get("url") and scraper:
                    async with httpx.AsyncClient() as client:
                        quote = await scraper.current_price(client, item["url"])
                if quote is None:
                    quotes = await self.market_quotes(item["query"], self.catalog.profile(item["query"]))
                    ok = sorted((q for q in quotes if q.accepted and not q.equivalent), key=lambda q: q.price)
                    quote = ok[0] if ok else None
                if quote is None:
                    log.info("Watchlist: sem preço para %s", item["name"])
                    continue
                await self.repo.record_watch_price(item["id"], quote.price, quote.store, quote.url)
                target = item.get("target_price")
                if target and quote.price <= float(target) and not await self.repo.recent_alert_for_watch(item["id"]):
                    text = format_watch_alert(item["name"], quote.price, float(target), quote.store, quote.url)
                    if await self.notifier.send(text):
                        await self.repo.save_alert(text, watch_id=item["id"])
                # Também roda a análise de revenda completa sobre o menor preço
                if item.get("min_profit"):
                    offer = Offer(title=item["query"], price=quote.price, source="watchlist",
                                  store=quote.store, url=quote.url, chat_title=f"Watchlist: {item['name']}")
                    if quotes is None:  # veio da URL exata: busca o mercado para comparar
                        quotes = await collect_quotes(self.sources, self.catalog.profile(item["query"]).query)
                    else:  # reaproveita as cotações já pagas (sem a própria oferta)
                        quotes = [q for q in quotes if q is not quote]
                    await self.handle_offer(offer, quotes)
            except Exception as exc:
                log.warning("Watchlist %s falhou: %s", item.get("name"), exc)
