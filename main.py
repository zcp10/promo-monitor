"""Promo Monitor — linha de comando.

  python main.py run                 # Telegram em tempo real + watchlist a cada N minutos
  python main.py listen              # só Telegram
  python main.py watch               # só watchlist (uma rodada)
  python main.py list-chats          # mostra ids dos seus grupos/canais
  python main.py backfill --limit 30 # analisa as últimas mensagens (teste)
  python main.py analyze "texto do post"   # testa um post sem Telegram
  python main.py doctor [--full]     # confere chaves, Supabase, bot e fontes de preço
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Windows: deixa o terminal imprimir acentos e emojis
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from promo.alerts import TelegramNotifier, brl
from promo.config import load_settings
from promo.pipeline import Pipeline
from promo.storage import build_repository


def build():
    settings = load_settings()
    repo = build_repository(settings)
    notifier = TelegramNotifier(settings.secrets.bot_token, settings.secrets.alert_chat_id)
    return settings, Pipeline(settings, repo, notifier)


async def watch_loop(settings, pipeline: Pipeline) -> None:
    minutes = int(settings.get("watchlist.check_every_minutes", 60))
    while True:
        await pipeline.check_watchlist()
        await asyncio.sleep(minutes * 60)


async def cmd_analyze(pipeline: Pipeline, text: str) -> None:
    offer = pipeline.extract(text)
    if offer is None:
        print("Não identifiquei uma oferta de produto nesse texto.")
        return
    print(f"Produto: {offer.title}\nPreço: {brl(offer.price)}  Antigo: {brl(offer.old_price)}  "
          f"Cupom: {offer.coupon}  Loja: {offer.store}  Unidades: {offer.quantity}")
    await pipeline.refresh_rules(force=True)
    a = await pipeline.evaluate(offer)
    if a.profile.get("grupo"):
        print(f"Grupo: {a.profile['grupo']['name']}")
    p = a.profile
    print(f"Categoria: {p.get('categoria') or '—'}  Top: {p.get('produto_top') or '—'}  "
          f"Comparação: {p.get('comparacao')}  Liquidez: {p.get('liquidez')}  Busca: \"{p.get('busca')}\"")
    print(f"\nCotações aceitas: {len(a.accepted_quotes)} de {len(a.quotes)}  (≈ = equivalente)")
    for q in sorted(a.accepted_quotes, key=lambda q: q.price)[:10]:
        mark = "≈" if q.equivalent else " "
        print(f" {mark}{brl(q.price):>14}  {q.match_score:.2f}  {(q.store or '')[:18]:18}  {q.title[:60]}")
    print(f"\nMediana: {brl(a.market_median)}  Menor: {brl(a.market_min)}  Confiança: {a.confidence}")
    for c in sorted(a.channels, key=lambda c: -c.profit):
        print(f"  {c.channel:24} venda {brl(c.sale_price):>12}  lucro {brl(c.profit):>12}  ROI {c.roi:6.0%}")
    print("\n" + ("✅ RENTÁVEL" if a.is_profitable else "❌ Não rentável: " + "; ".join(a.reasons)))


def _supabase_key_role(key: str) -> str | None:
    """Tipo da chave Supabase, sem expor o valor: service_role, anon, secret, publishable."""
    import base64
    import json

    if key.startswith("sb_secret_"):
        return "secret"
    if key.startswith("sb_publishable_"):
        return "publishable"
    try:
        payload = key.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))).get("role")
    except (IndexError, ValueError):
        return None


async def cmd_doctor(settings, pipeline: Pipeline, spend_credits: bool) -> None:
    """Confere cada peça: chaves no .env, Supabase, bot de alertas, fontes de preço."""
    import httpx

    s = settings.secrets
    ok = lambda b: "OK  " if b else "FALTA"
    print("== Variáveis do .env ==")
    for name, val in [("TG_API_ID", s.tg_api_id), ("TG_API_HASH", s.tg_api_hash),
                      ("TG_BOT_TOKEN", s.bot_token), ("TG_ALERT_CHAT_ID", s.alert_chat_id),
                      ("SUPABASE_URL", s.supabase_url), ("SUPABASE_SERVICE_ROLE_KEY", s.supabase_service_key),
                      ("HASDATA_API_KEY", s.hasdata_api_key), ("MELI_ACCESS_TOKEN (opcional)", s.meli_access_token),
                      ("ANTHROPIC_API_KEY (opcional)", s.anthropic_api_key)]:
        print(f"  [{ok(bool(val))}] {name}")
    groups = settings.get("telegram.groups", []) or []
    print(f"  Grupos configurados: {len(groups)} {'(troque o exemplo!)' if any('exemplo' in str(g) for g in groups) else ''}")

    async with httpx.AsyncClient(timeout=20) as client:
        print("\n== Supabase ==")
        if s.supabase_url and s.supabase_service_key:
            if _supabase_key_role(s.supabase_service_key) in ("anon", "publishable"):
                print("  ERRO: SUPABASE_SERVICE_ROLE_KEY é a chave PÚBLICA (anon/publishable). "
                      "Use a service_role (Legacy API Keys) ou a Secret key.")
            try:
                r = await client.get(f"{s.supabase_url.rstrip('/')}/rest/v1/offers",
                                     params={"select": "id", "limit": "1"},
                                     headers={"apikey": s.supabase_service_key,
                                              "Authorization": f"Bearer {s.supabase_service_key}"})
                print(f"  HTTP {r.status_code} " + ("OK — tabela offers acessível" if r.status_code == 200
                                                  else r.text[:200]))
            except Exception as exc:
                print(f"  ERRO: {exc}")
        else:
            print("  Não configurado — usando SQLite local (promo_monitor.db)")

        print("\n== Bot de alertas ==")
        sent = await pipeline.notifier.send("✅ Radar de Revenda: teste de alerta (comando doctor).")
        print("  Mensagem de teste enviada — confira seu Telegram." if sent else
              "  Não enviou. Confira TG_BOT_TOKEN, TG_ALERT_CHAT_ID e se você mandou /start ao bot.")

        print("\n== Mercado Livre (API) ==")
        try:
            headers = {"Authorization": f"Bearer {s.meli_access_token}"} if s.meli_access_token else {}
            r = await client.get("https://api.mercadolibre.com/sites/MLB/search",
                                 params={"q": "galaxy a55", "limit": 1}, headers=headers)
            print(f"  HTTP {r.status_code} " + ("OK" if r.status_code == 200 else
                  "— busca recusada; fonte será pulada (Google Shopping cobre)"))
        except Exception as exc:
            print(f"  ERRO: {exc}")

        print("\n== Google Shopping (HasData) ==")
        if not s.hasdata_api_key:
            print("  Sem HASDATA_API_KEY.")
        elif not spend_credits:
            print("  Pulado para não gastar créditos. Rode `python main.py doctor --full` para testar (~10 créditos).")
        else:
            from promo.pricing import GoogleShoppingSource
            try:
                quotes = await GoogleShoppingSource(s.hasdata_api_key).search(client, "samsung galaxy a55 256gb")
                print(f"  OK — {len(quotes)} preços. Ex.: " +
                      "; ".join(f"{q.store}: {brl(q.price)}" for q in quotes[:3]))
            except Exception as exc:
                print(f"  ERRO: {exc}")

    print("\n== Telegram (sua conta) ==")
    try:
        import telethon  # noqa: F401
        session = Path(f"{s.tg_session}.session")
        print("  Telethon instalado. " + ("Sessão encontrada (login feito)." if session.exists() else
              "Sem sessão: rode `python main.py list-chats` no SEU terminal para fazer login."))
    except ImportError:
        print("  Telethon não instalado: pip install -r requirements.txt")


def main() -> None:
    ap = argparse.ArgumentParser(description="Monitor de promoções para revenda")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run"); sub.add_parser("listen"); sub.add_parser("watch"); sub.add_parser("list-chats")
    bf = sub.add_parser("backfill"); bf.add_argument("--limit", type=int, default=30)
    bf.add_argument("--groups", type=int, nargs="+", help="ids dos grupos (padrão: todos do settings.yaml)")
    an = sub.add_parser("analyze"); an.add_argument("text")
    dr = sub.add_parser("doctor"); dr.add_argument("--full", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--log-file", help="grava o log neste arquivo (útil com pythonw, sem janela)")
    args = ap.parse_args()

    log_kwargs = {}
    if args.log_file:
        Path(args.log_file).parent.mkdir(parents=True, exist_ok=True)
        log_kwargs["handlers"] = [logging.FileHandler(args.log_file, encoding="utf-8")]
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%d/%m %H:%M:%S",
                        **log_kwargs)
    # httpx loga a URL inteira em INFO; a URL da API de bots contém o token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings, pipeline = build()

    from promo import telegram_listener as tl

    async def run_all():
        await asyncio.gather(tl.listen(settings, pipeline), watch_loop(settings, pipeline))

    commands = {
        "run": run_all,
        "listen": lambda: tl.listen(settings, pipeline),
        "watch": pipeline.check_watchlist,
        "list-chats": lambda: tl.list_chats(settings),
        "backfill": lambda: tl.backfill(settings, pipeline, args.limit, args.groups),
        "analyze": lambda: cmd_analyze(pipeline, args.text),
        "doctor": lambda: cmd_doctor(settings, pipeline, args.full),
    }
    try:
        asyncio.run(commands[args.cmd]())
    except KeyboardInterrupt:
        print("\nEncerrado.")


if __name__ == "__main__":
    main()
