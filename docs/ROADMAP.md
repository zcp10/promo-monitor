🌐 **Language / Idioma:** **English** & **Português (BR)**

# Engineering Roadmap & Technical Debt

> **Context:** Promo Monitor began as an experimental test to check whether automated deal parsing + reference pricing could spot profitable resale opportunities reliably. After evolving into a modular pipeline and spending time paused while evaluating real-world scraping constraints, development has resumed with a focus on **resilience, stability, and clean extensibility**.
>
> **Contexto:** O Promo Monitor nasceu como um teste experimental para validar se a extração automática de promoções combinada a preços de referência conseguiria identificar oportunidades reais de revenda. Após evoluir para um pipeline modular e passar por uma pausa para reavaliar limites de coleta, o desenvolvimento foi retomado com foco em **resiliência, estabilidade e extensibilidade**.

---

## 1. Connectivity & Resilience / Conexão e Resiliência

| Item | Current Gap / Problema Atual | Planned Upgrade / Melhoria Planejada | Target Modules |
|---|---|---|---|
| **HTTP Retry & Circuit Breakers** | `GoogleShoppingSource` uses a flat 3600s pause on `401/402/403/429`; no jittered exponential backoff or circuit breaker across providers. | Differentiate auth failures (`401/403`) from rate limits (`429/5xx`), add exponential backoff with jitter, and track provider health state. | `promo/pricing.py`, `promo/reef.py` |
| **Telegram Reconnect Supervisor** | `listen()` calls `client.run_until_disconnected()` once without reconnect backoff or heartbeat checks; full `asyncio.Queue(maxsize=500)` silently drops deals. | Add supervised reconnect loop, connection state logging, and queue overflow alerting/backpressure handling. | `promo/telegram_listener.py` |
| **Notifier Connection Pooling** | `TelegramNotifier.send()` creates a new `httpx.AsyncClient` on every alert without retry on transient network/429 errors. | Reuse a shared async `httpx` client and retry transient Telegram Bot API failures honoring `retry_after`. | `promo/alerts.py` |
| **Mercado Livre OAuth Refresh** | `MeliSource` relies on a manually refreshed `MELI_ACCESS_TOKEN`. | Implement OAuth2 refresh-token rotation so official Mercado Livre catalog pricing works unattended. | `promo/pricing.py`, `promo/config.py` |

---

## 2. Stability & Observability / Estabilidade e Observabilidade

| Item | Current Gap / Problema Atual | Planned Upgrade / Melhoria Planejada | Target Modules |
|---|---|---|---|
| **Persistent SQLite Cache & Dedup** | `SQLiteRepository` stores `market_cache` only in an in-memory `_cache` dict; `Pipeline._recent` dedup is also memory-only. | Persist `market_cache` and recent-offer hashes in SQLite tables so offline/local runs retain 30-day cache across restarts. | `promo/storage.py`, `promo/pipeline.py` |
| **Structured Telemetry & Metrics** | Matcher rejections, price-source fallbacks, and latency are logged as unstructured text. | Emit structured JSON logs and counters (cache hit rate, match acceptance ratio, per-provider error/block rate). | `promo/pipeline.py`, `promo/matcher.py` |
| **Matcher & Parser Edge-Case Suite** | Sibling SKUs (storage/color/generation) and multi-item bundles still produce occasional edge-case mismatches. | Expand parameterized offline unit tests covering ambiguous titles, bundles, and Brazilian promo slang. | `tests/`, `promo/matcher.py`, `promo/parser.py` |
| **Lint & Static Type Enforcement** | Minor `ruff` warnings exist and `mypy` is not yet part of CI. | Clean up existing `ruff` findings, add type annotations across `promo/`, and enforce `ruff` + `mypy` in GitHub Actions. | `pyproject.toml`, `.github/workflows/ci.yml` |

---

## 3. Integration & Extensibility / Facilidade de Integração e Portabilidade

| Item | Current Gap / Problema Atual | Planned Upgrade / Melhoria Planejada | Target Modules |
|---|---|---|---|
| **Pluggable Pipeline Protocols** | `Pipeline` directly depends on Telethon input and Telegram Bot alerts, complicating non-Telegram integrations. | Extract `OfferSource`, `PriceProvider`, and `NotificationChannel` protocols so Discord, RSS, webhooks, Slack, or email adapters plug in cleanly. | `promo/pipeline.py`, `promo/models.py` |
| **Cross-Platform Docker Compose** | Isolated browser pilot (`browser/pilot/`) relies on Windows PowerShell (`pilot.ps1`) and Windows Docker Desktop named pipes. | Add a cross-platform `docker-compose.yml` running the monitor and isolated browser service together on Linux, macOS, and Windows. | `browser/pilot/`, root `docker-compose.yml` |
| **Local Evaluation API / Webhook** | CLI `main.py analyze` handles one post at a time; no local HTTP endpoint for external scripts or automation tools. | Expose an optional lightweight FastAPI/Starlette local endpoint (or batch JSON stdin/stdout mode) to evaluate offers from external tools. | `main.py`, `promo/` |
| **Automated Fee-Table Validation** | Marketplace fee rules in `config/settings.yaml` are maintained manually. | Add schema validation and periodic smoke checks for channel fee bands and tax settings. | `promo/profit.py`, `config/settings.yaml` |
