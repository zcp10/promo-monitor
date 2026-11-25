# Google Shopping — piloto local

Componente experimental de coleta local no Google Shopping. **Opcional e sujeito a bloqueios anti-bot.**

## Comportamento e bloqueios

Consultas automatizadas ao Google Shopping podem receber bloqueio por tráfego incomum ("Nossos sistemas detectaram tráfego incomum na sua rede de computadores") ou CAPTCHA. Quando isso ocorre, a rodada é interrompida imediatamente e o coletor entra em pausa de cooldown, sem tentar resolver verificações automaticamente.

Os testes de DOM controlado e os testes unitários Python validam as regras de limite, o parser e a integração no pipeline, **não a estabilidade permanente dos seletores HTML do Google Shopping**.

## O que foi construído

- `browser/google-shopping.cjs`: controlador Playwright que abre o Google Shopping, extrai cartões visíveis e lê nome, preço atual em reais, loja e link quando presentes no mesmo cartão. Não abre cada loja individualmente nem rola automaticamente para "mais lojas".
- `promo/browser_shopping.py`: adaptador assíncrono para o monitor Python.
- Integração sequencial: usado apenas quando a fonte gratuita inicial (KaBuM) não retorna cotações compatíveis, mantendo fontes pagas como reserva.
- O extrator é conservador: descarta blocos com vários preços atuais ambíguos, preços de parcela, frete isolado, preços riscados e cartões sem loja identificável. Frete desconhecido permanece `null`, nunca zero.

## Limites operacionais padrão

| Controle | Valor |
|---|---|
| Concorrência | Uma consulta por diretório de estado, incluindo processos separados |
| Intervalo entre início de tentativas | Pelo menos 60 segundos |
| Máximo por janela móvel de uma hora | 5–10 tentativas (configurável em `settings.yaml`) |
| Cache de resultados válidos por consulta exata | 24 horas a 30 dias |
| Pausa após bloqueio/CAPTCHA/HTTP 403 ou 429 | 24 horas (com backoff progressivo no piloto isolado) |

Não alterna perfis pessoais, IPs ou proxies para contornar bloqueios. Mais informações do Google sobre detecção de tráfego incomum: https://support.google.com/websearch/answer/86640?hl=pt-BR

## Instalação e testes offline

Requisitos: Node.js 18+ e Python 3.11+. Para instalar as dependências JS do navegador sem baixar binários extras:

```powershell
npm ci --prefix browser --ignore-scripts --no-audit --no-fund
```

Os testes locais usam apenas HTML sintético e fontes simuladas em memória, sem acessar a rede nem depender de `.env`:

```powershell
node tests/browser.test.cjs
python -m unittest discover -s tests -q
```

Em ambiente local com execução ativa, dados de perfil e estado ficam em pastas ignoradas pelo Git (`.shopping-browser/`), nunca no repositório.
