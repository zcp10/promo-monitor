🌐 **Language / Idioma:** **English** | [Português (BR)](README.pt-BR.md)

# Promo Monitor

**Promo Monitor** is an automated resale intelligence pipeline for the Brazilian retail market. It listens to Telegram deal channels in real time, extracts structured product offers, gathers market reference prices sequentially from multiple sources, validates product-to-product equivalence, computes net profit and ROI after Brazilian marketplace fees and taxes, and sends actionable alerts via Telegram.

> **Project Origin & Status:** This project started as an **experimental prototype / technical test** to validate whether noisy Telegram deal feeds could be automatically cross-checked against retail reference prices and Brazilian marketplace fees. It grew organically into a working multi-module pipeline, was **paused for a period** to reassess scraping, matching, and architecture trade-offs, and has **recently been resumed** for structured engineering work. It is an active work in progress — functional at its core, with documented rough edges, technical debt, and an ongoing roadmap.

---

## Architecture

```text
Telegram Channels ──▶ Parser (Rules + optional AI) ──▶ Title Filter & Catalog
                                                               │
                                                               ▼
Telegram Alerts ◀── Profit & ROI Engine ◀── Matcher ◀── Sequential Pricing
                         │                               (Cache → KaBuM → Browser → HasData → Reef)
                         ▼
               Storage (Supabase / SQLite) ──▶ Static Dashboard (dashboard/)
```

### Package Overview (`promo/`)

- `promo/config.py` — Loads `config/settings.yaml` and environment variables from `.env` (with configurable paths for local session and SQLite database).
- `promo/models.py` — Core dataclasses (`Offer`, `Quote`, `ChannelResult`, `Analysis`).
- `promo/telegram_listener.py` — Telethon user-client listener for configured Telegram promo groups/channels.
- `promo/parser.py` — Deterministic regex/heuristic parser for Brazilian promo posts (price, old price, coupon, store, multi-unit quantity) with optional Anthropic AI fallback.
- `promo/title_filter.py` — Filters out non-product headlines, promo chatter, and ignored keywords before spending lookup budget.
- `promo/catalog.py` — Maps titles to categories, liquidity tiers, and search/equivalence profiles using `config/categories.yaml`.
- `promo/pricing.py` — Sequential quote fetcher with 30-day reference caching, rate limits, and source fallback (`KaBuM` → `google_browser` → `HasData` → `ReefAPI`).
- `promo/browser_shopping.py` — Async adapter for Playwright-based Google Shopping extraction (`browser/`).
- `promo/scrapling_browser.py` — Client for the isolated Dockerized Scrapling browser pilot (`browser/pilot/`).
- `promo/reef.py` — ReefAPI client for Buscapé/Zoom reference quotes.
- `promo/matcher.py` — Token/model-aware product matcher (rejects accessories, used/refurbished items when unwanted, and different product lines such as `Tune` vs `Live`).
- `promo/profit.py` — Net profit and ROI calculator across `mercadolivre_classico`, `mercadolivre_premium`, `shopee`, and `venda_direta` (taxes, packaging, fixed fee bands, shipping, and risk buffer).
- `promo/storage.py` — Unified repository interface supporting Supabase PostgREST in production and local SQLite (`data/promo_monitor.db`) for offline/testing use.
- `promo/alerts.py` — Sends formatted deal and watchlist alerts through the Telegram Bot API.
- `promo/pipeline.py` — Orchestrates extraction, caching, sequential pricing, matching, profit evaluation, persistence, and alerting.

---

## Engineering Decisions & Trade-offs

1. **Sequential Pricing with Paid-API Reserve (`pricing.py`, `pipeline.py`)**
   - *Decision:* Query free/cached sources first (30-day reference cache → public KaBuM search → isolated browser collector) and only fall back to paid APIs (`HasData`, `ReefAPI`) when no compatible quotes are found, subject to strict daily caps.
   - *Trade-off:* Higher latency on cache misses in exchange for predictable, near-zero API cost on noisy feeds.

2. **Conservative Product Matching (`matcher.py`, `catalog.py`)**
   - *Decision:* Require category/model token compatibility and reject accessories, cases, and ambiguous multi-price cards rather than relying on fuzzy string similarity alone.
   - *Trade-off:* Higher false-negative rate on messy titles, but avoids false-positive profit alerts caused by comparing a phone to a case or a different model generation.

3. **Marketplace Fee & Risk Modeling in YAML (`profit.py`, `config/settings.yaml`)**
   - *Decision:* Parameterize per-channel commissions, fixed fee bands (e.g., Mercado Livre sub-R\$ 79 fixed fee), seller shipping share, tax rate, packaging, and a return/damage risk buffer in YAML rather than hardcoding.
   - *Trade-off:* Requires periodic updates when marketplaces adjust fee tables, but keeps business rules decoupled from code.

4. **Lightweight Persistence (`storage.py`, `supabase/migrations/`)**
   - *Decision:* Use a minimal repository protocol backed by `httpx` against Supabase PostgREST (avoiding heavy SDKs) with automatic local SQLite fallback when Supabase credentials are absent.
   - *Trade-off:* Explicit SQL migrations (`supabase/migrations/*.sql`) with Row Level Security (RLS) instead of ORM auto-migrations.

---

## Current Issues & Known Limitations

Because this project began as an experimental prototype and was recently resumed after a pause, several concrete technical limitations remain:

### Connectivity & Resilience
- **Google Shopping anti-bot friction:** Automated browser queries frequently trigger CAPTCHA or unusual-traffic blocks (`HTTP 429` / `/sorry`). The pipeline enforces strict cooldowns and falls back to alternative sources rather than bypassing protections.
- **Telegram session & queue resilience (`promo/telegram_listener.py`):** Long-running `listen()` sessions lack an automatic reconnect/heartbeat supervisor on unstable connections and silently drop incoming posts if the bounded `asyncio.Queue(maxsize=500)` fills up.
- **Per-alert HTTP client & missing retries (`promo/alerts.py`, `promo/pricing.py`):** `TelegramNotifier.send()` opens a fresh `httpx.AsyncClient` per alert without retry/backoff on transient failures, while `GoogleShoppingSource` applies a flat 1-hour cooldown instead of distinguishing `401/403` auth errors from `429` rate limits with exponential backoff.
- **Manual Mercado Livre auth:** Official API integration expects a static `MELI_ACCESS_TOKEN` and lacks an automated OAuth refresh-token flow.
- **Fragile DOM/HTML extractors:** Public page parsers (KaBuM, Google Shopping) break whenever upstream markup changes.

### Stability & Persistence
- **Volatile SQLite cache (`promo/storage.py`):** `SQLiteRepository` keeps `market_cache` only in an in-memory dictionary (`_cache`) instead of persisting it in SQLite, so cached quotes are lost on process restart when running offline. `Pipeline._recent` offer deduplication is likewise in-memory only.
- **Windows-coupled browser pilot (`browser/pilot/`):** The Dockerized browser pilot bootstrap (`pilot.ps1`, `start_on_login.py`) assumes Windows PowerShell and Docker Desktop named pipes, limiting cross-platform deployment.
- **Limited observability & lint/type debt:** Rejections in `matcher.py` and source errors are emitted as unstructured text logs; minor `ruff` warnings remain and `mypy` type checking is not yet enforced in CI.

### Integration & Extensibility
- **Tightly coupled orchestration (`promo/pipeline.py`):** `Pipeline` directly instantiates and wires Telethon, `TelegramNotifier`, and specific pricing providers rather than consuming abstract `OfferSource`, `PriceProvider`, and `NotificationChannel` protocols—making it harder to add Discord/RSS inputs or webhook/Slack outputs.
- **Complex post formats & sibling variants:** Multi-item bundles, conditional coupons, image-only deals, and closely named hardware/storage SKUs remain challenging edge cases.

---

## Continuous Improvement Roadmap

See [docs/ROADMAP.md](docs/ROADMAP.md) for the full breakdown of planned refactoring and upcoming features across three pillars:

1. **Connectivity & Resilience:** Exponential backoff with jitter and per-provider circuit breakers in `promo/pricing.py`; HTTP connection pooling and alert retries in `promo/alerts.py`; Telethon reconnect watchdog + backpressure metrics; Mercado Livre OAuth refresh-token support.
2. **Stability & Observability:** Persistent `market_cache` and deduplication tables in `SQLiteRepository`; expanded edge-case unit tests for `matcher.py` and `parser.py`; structured JSON logging with per-source hit/rejection telemetry; resolving `ruff` debt and enforcing `ruff` + `mypy` in GitHub Actions CI.
3. **Easier Integration & Portability:** Decoupling `Pipeline` behind `OfferSource`, `PriceProvider`, and `NotificationChannel` protocols; cross-platform `docker-compose.yml` uniting the monitor and isolated browser pilot; lightweight local HTTP/CLI evaluation API for external tools and webhooks.

---

## Quickstart

### Requirements
- Python 3.11+
- Node.js 18+ (optional, only for `browser/` Playwright scripts and JS tests)
- Docker (optional, only for `browser/pilot/` isolated collector)

### Installation

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

### Configuration

1. Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   cp .env.example .env
   ```
2. Edit `config/settings.yaml` to add your target Telegram group usernames or IDs (run `python main.py list-chats` after configuring Telegram credentials).

### Running

```bash
# Check configuration, database, bot, and pricing sources
python main.py doctor

# Analyze a single promo post from the CLI (works offline with SQLite fallback)
python main.py analyze "Fone JBL Tune 770NC por R$ 299 na Amazon"

# Run the full monitor (Telegram real-time listener + periodic watchlist)
python main.py run
```

---

## Testing

Unit tests run completely offline without `.env` or external credentials (using in-memory SQLite and fake pricing sources):

```bash
python -m unittest discover -s tests -q
```

To run the browser DOM extractor tests (Node.js):

```bash
npm ci --prefix browser --ignore-scripts --no-audit --no-fund
node tests/browser.test.cjs
```

---

## Disclaimer

This project is provided for **educational and personal research purposes**. When using any web or API integration, respect the Terms of Service and `robots.txt` of the monitored platforms. Provided as-is without warranty of any kind.
