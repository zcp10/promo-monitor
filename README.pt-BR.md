🌐 **Idioma / Language:** [English](README.md) | **Português (BR)**

# Promo Monitor

O **Promo Monitor** é um pipeline automatizado de inteligência para revenda no varejo brasileiro. Ele monitora canais de promoções no Telegram em tempo real, extrai ofertas estruturadas de produtos, consulta preços de referência no mercado de forma sequencial em múltiplas fontes, valida a equivalência entre produtos, calcula lucro líquido e ROI já descontando taxas de marketplaces brasileiros e impostos, e envia alertas acionáveis pelo Telegram.

> **Origem e Status do Projeto:** Este projeto começou inicialmente como um **teste / protótipo experimental** para validar se feeds ruidosos de promoções no Telegram poderiam ser cruzados automaticamente com preços de referência e taxas de marketplaces brasileiros. Ele evoluiu organicamente para um pipeline modular funcional, **ficou parado por um período** para reavaliação de coleta, pareamento e arquitetura, e **foi retomado recentemente** para evolução contínua. É um trabalho em andamento — funcional no núcleo, com limitações conhecidas e um roadmap claro pela frente.

---

## Arquitetura

```text
Canais do Telegram ──▶ Parser (Regras + IA opcional) ──▶ Filtro de Título e Catálogo
                                                                   │
                                                                   ▼
Alertas Telegram ◀── Motor de Lucro e ROI ◀── Matcher ◀── Precificação Sequencial
                           │                               (Cache → KaBuM → Browser → HasData → Reef)
                           ▼
                 Persistência (Supabase / SQLite) ──▶ Painel Estático (dashboard/)
```

### Visão Geral do Pacote (`promo/`)

- `promo/config.py` — Carrega `config/settings.yaml` e variáveis de ambiente do `.env` (com caminhos configuráveis para sessão local e banco SQLite).
- `promo/models.py` — Dataclasses centrais (`Offer`, `Quote`, `ChannelResult`, `Analysis`).
- `promo/telegram_listener.py` — Listener Telethon (user-client) para grupos/canais de promoção configurados.
- `promo/parser.py` — Parser determinístico por regex/heurísticas para posts brasileiros (preço, preço antigo, cupom, loja, quantidade) com fallback opcional de IA (Anthropic).
- `promo/title_filter.py` — Descarta chamadas genéricas, conversas e palavras ignoradas antes de gastar orçamento de busca.
- `promo/catalog.py` — Mapeia títulos para categorias, níveis de liquidez e regras de busca/equivalência via `config/categories.yaml`.
- `promo/pricing.py` — Busca sequencial de cotações com cache de referência de 30 dias, limites de taxa e fallback (`KaBuM` → `google_browser` → `HasData` → `ReefAPI`).
- `promo/browser_shopping.py` — Adaptador assíncrono para coleta no Google Shopping via Playwright (`browser/`).
- `promo/scrapling_browser.py` — Cliente para o piloto isolado em Docker com Scrapling (`browser/pilot/`).
- `promo/reef.py` — Cliente ReefAPI para cotações de referência no Buscapé/Zoom.
- `promo/matcher.py` — Pareador sensível a tokens e modelos (rejeita acessórios, usados/recondicionados quando indesejados e linhas diferentes como `Tune` vs `Live`).
- `promo/profit.py` — Calculadora de lucro líquido e ROI para `mercadolivre_classico`, `mercadolivre_premium`, `shopee` e `venda_direta` (impostos, embalagem, faixas de taxa fixa, frete e reserva de risco).
- `promo/storage.py` — Interface unificada de repositório suportando Supabase PostgREST em produção e SQLite local (`data/promo_monitor.db`) para uso offline/testes.
- `promo/alerts.py` — Envia alertas formatados de ofertas e watchlist via Telegram Bot API.
- `promo/pipeline.py` — Orquestra extração, cache, precificação sequencial, pareamento, cálculo de lucro, persistência e alertas.

---

## Decisões de Engenharia e Trade-offs

1. **Precificação Sequencial com Reserva de API Paga (`pricing.py`, `pipeline.py`)**
   - *Decisão:* Consultar primeiro fontes gratuitas ou em cache (cache de 30 dias → busca pública na KaBuM → coletor isolado em navegador) e acionar APIs pagas (`HasData`, `ReefAPI`) apenas quando não há cotações compatíveis, respeitando tetos diários rígidos.
   - *Trade-off:* Latência um pouco maior em *cache miss* em troca de custo de API previsível e próximo de zero em feeds ruidosos.

2. **Pareamento Conservador de Produtos (`matcher.py`, `catalog.py`)**
   - *Decisão:* Exigir compatibilidade de categoria/modelo e descartar acessórios, capas e cartões com múltiplos preços ambíguos em vez de confiar apenas em similaridade textual difusa.
   - *Trade-off:* Maior taxa de falsos negativos em títulos bagunçados, mas evita falsos positivos de lucro causados por comparar um celular com uma capinha ou com outra geração do modelo.

3. **Modelagem de Taxas e Risco em YAML (`profit.py`, `config/settings.yaml`)**
   - *Decisão:* Parametrizar comissões por canal, faixas de taxa fixa (ex.: taxa fixa do Mercado Livre abaixo de R\$ 79), coparticipação no frete, alíquota de imposto, embalagem e reserva para devoluções/avarias em YAML.
   - *Trade-off:* Exige atualização periódica quando marketplaces mudam suas tabelas, mas mantém as regras de negócio desacopladas do código.

4. **Persistência Leve (`storage.py`, `supabase/migrations/`)**
   - *Decisão:* Abstrair a persistência atrás de um protocolo leve usando `httpx` direto no PostgREST do Supabase (sem SDKs pesados) com fallback automático para SQLite local na ausência de credenciais.
   - *Trade-off:* Migrações SQL explícitas (`supabase/migrations/*.sql`) com Row Level Security (RLS) em vez de ORM.

---

## Problemas Atuais e Limitações Conhecidas

Por ter nascido como um protótipo de teste e ter sido retomado recentemente após um período parado, o projeto ainda possui limitações reais mapeadas no código:

### Conexão e Dependências Externas
- **Bloqueios anti-bot no Google Shopping:** Consultas automatizadas frequentemente acionam CAPTCHA ou bloqueio por tráfego incomum (`HTTP 429` / `/sorry`). O pipeline aplica pausas rígidas (*cooldown*) e recorre às fontes alternativas em vez de burlar proteções.
- **Resiliência da sessão e fila do Telegram (`promo/telegram_listener.py`):** O `listen()` não possui supervisor de reconexão automática/*heartbeat* em conexões instáveis e descarta mensagens silenciosamente se a fila `asyncio.Queue(maxsize=500)` encher.
- **Cliente HTTP por alerta e ausência de retry (`promo/alerts.py`, `promo/pricing.py`):** `TelegramNotifier.send()` cria um novo `httpx.AsyncClient` a cada alerta sem retry/backoff, e `GoogleShoppingSource` usa um cooldown fixo de 1 hora sem diferenciar erros `401/403` de `429` com *exponential backoff*.
- **Autenticação manual do Mercado Livre:** A integração oficial exige token OAuth manual (`MELI_ACCESS_TOKEN`) e ainda não possui renovação automática (*refresh token*).
- **Seletores HTML/DOM frágeis:** Extratores de páginas públicas (KaBuM, Google Shopping) dependem do layout atual e quebram se o HTML mudar.

### Estabilidade e Persistência
- **Cache volátil no SQLite (`promo/storage.py`):** `SQLiteRepository` mantém `market_cache` apenas em um dicionário em memória (`_cache`), perdendo o cache ao reiniciar o processo no modo offline; a deduplicação `Pipeline._recent` também é só em memória.
- **Piloto isolado acoplado ao Windows (`browser/pilot/`):** Os scripts de inicialização (`pilot.ps1`, `start_on_login.py`) dependem de PowerShell e named pipes do Docker Desktop no Windows, dificultando rodar em servidores Linux/macOS.
- **Observabilidade limitada e dívida de lint/tipagem:** Rejeições no `matcher.py` e falhas entre provedores geram apenas logs em texto simples; há avisos menores do `ruff` e falta checagem estática (`mypy`) no CI.

### Integração e Casos de Borda
- **Orquestração acoplada (`promo/pipeline.py`):** `Pipeline` instancia diretamente Telethon, `TelegramNotifier` e fontes de preço específicas em vez de receber protocolos plugáveis (`OfferSource`, `PriceProvider`, `NotificationChannel`), dificultando plugar Discord, RSS, webhooks ou novas lojas.
- **Formatos complexos e modelos irmãos:** Combos multi-item, cupons condicionais, posts só em imagem e variantes muito próximas (capacidade/SKU) ainda exigem evolução.

---

## Melhorias Contínuas Planejadas (Roadmap)

Veja o detalhamento técnico completo em [docs/ROADMAP.md](docs/ROADMAP.md), dividido em três frentes contínuas:

1. **Conexão e Resiliência:** *Exponential backoff* com jitter e *circuit breakers* por fonte em `promo/pricing.py`; pool de conexões HTTP e retries em `promo/alerts.py`; supervisor de reconexão no Telethon com monitoramento de fila; fluxo OAuth com *refresh token* para o Mercado Livre.
2. **Estabilidade e Observabilidade:** Tabela real de `market_cache` e deduplicação persistente no `SQLiteRepository`; ampliação dos testes de borda em `matcher.py` e `parser.py`; logs estruturados (JSON) com taxa de acerto/rejeição por fonte; resolução dos avisos do `ruff` e exigência de `ruff` + `mypy` no CI.
3. **Facilidade de Integração e Portabilidade:** Desacoplamento do `Pipeline` por meio de protocolos (`OfferSource`, `PriceProvider`, `NotificationChannel`); `docker-compose.yml` multiplataforma unindo monitor e coletor isolado; API HTTP/CLI local leve para avaliação sob demanda e integração via webhooks.

---

## Início Rápido

### Requisitos
- Python 3.11+
- Node.js 18+ (opcional, apenas para scripts Playwright em `browser/` e testes JS)
- Docker (opcional, apenas para o coletor isolado em `browser/pilot/`)

### Instalação

```bash
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# Linux/macOS:
# source .venv/bin/activate

pip install -r requirements.txt
```

### Configuração

1. Copie `.env.example` para `.env` e preencha suas credenciais:
   ```bash
   cp .env.example .env
   ```
2. Edite `config/settings.yaml` com os `@usernames` ou IDs dos grupos do Telegram (execute `python main.py list-chats` após configurar o Telegram).

### Execução

```bash
# Verifica configuração, banco, bot e fontes de preço
python main.py doctor

# Analisa um post individual pelo terminal (funciona offline com SQLite)
python main.py analyze "Fone JBL Tune 770NC por R$ 299 na Amazon"

# Executa o monitor completo (listener em tempo real + watchlist periódica)
python main.py run
```

---

## Testes

Os testes unitários rodam 100% offline sem `.env` nem credenciais externas (usando SQLite em memória e fontes simuladas):

```bash
python -m unittest discover -s tests -q
```

Para rodar os testes do extrator de navegador (Node.js):

```bash
npm ci --prefix browser --ignore-scripts --no-audit --no-fund
node tests/browser.test.cjs
```

---

## Aviso Legal

Este projeto é disponibilizado para **fins educacionais e de pesquisa pessoal**. Ao utilizar qualquer integração web ou de API, respeite os Termos de Uso e o `robots.txt` das plataformas monitoradas. Fornecido no estado em que se encontra, sem garantias.
