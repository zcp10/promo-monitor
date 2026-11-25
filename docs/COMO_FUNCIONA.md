# Radar de Revenda — como funciona

> Resumo arquitetural do projeto. Para instalar e configurar, veja `README.pt-BR.md` (ou `README.md`).

## Para que serve

O Radar lê automaticamente os grupos de promoção do Telegram que você acompanha e, para cada oferta, responde uma pergunta só:

**"Se eu comprar isto agora e revender, quanto sobra no bolso?"**

Quando a resposta é "vale a pena", ele avisa você no Telegram e mostra os detalhes no painel.

---

## O caminho de uma oferta

```
Post no grupo do Telegram
   ↓  1. Leitura do post
Produto, preço, cupom, loja, quantidade
   ↓  2. Classificação (catálogo)
Categoria, especificações, se é "produto top", liquidez
   ↓  3. Preço de mercado
KaBuM (grátis) → só se não houver preço compatível: Google Shopping no Chrome
   ↓  4. Conta da revenda
Lucro por canal: Mercado Livre, Shopee, venda direta (OLX/Facebook/WhatsApp)
   ↓  5. Decisão
Rentável? → alerta no Telegram + painel   |   Não? → só fica registrado
```

### 1. Leitura do post
O monitor usa a sua conta do Telegram (apenas leitura, nunca escreve nos grupos) e ouve os grupos configurados em `config/settings.yaml`.
De cada post ele tira: nome do produto, preço final, preço antigo, cupom, loja, link e **quantidade**
("5 fones por R$ 50" vira 5 unidades de R$ 10).

Ele ignora posts que não são produto (cupons soltos, frases de chamada como "SOM DE RESPEITO!") e características que parecem quantidade mas não são ("Gabinete com **3x** Cooler").

O filtro de títulos escolhe a linha com mais evidência de produto (categoria, marca e modelo), mesmo que uma chamada publicitária apareça antes. "Combina com tudo" e "super promoção" são ignorados como frases completas; não bloqueiam um post que tenha o nome real do produto em outra linha. Prefixos como "baixou" e "voltou" são removidos do nome. Em TVs, o código de modelo escrito no post é preservado também na consulta. As frases adicionais podem ser configuradas em `title_filter.ignored_headlines`, sem usar a lista global de palavras que bloqueia posts inteiros.

O Qwen local foi medido com três exemplos, mas não participa automaticamente de todas as ofertas. As correções de título acima usam regras locais, sem inferência de IA. Uma eventual assistência por IA deve ser restrita a textos ambíguos e validar marca/modelo contra o texto original.

### 2. Classificação
O arquivo `config/categories.yaml` tem **43 categorias** e **~107 produtos top de revenda** (iPhone, Galaxy, RTX, Ryzen, SSD, PS5, AirPods, JBL…). Cada categoria define:

| Regra | Exemplo |
|---|---|
| **Comparar por especificação** (marca não importa) | SSD NVMe 1TB, memória DDR4 16GB, monitor 21,5" Full HD |
| **Comparar por modelo** (marca/modelo importam) | RTX 5060, Galaxy A56, JBL Tune 520BT |
| **Liquidez** (quão rápido vende) | alta = celular, GPU, SSD · baixa = cadeira, ar-condicionado |

### 3. Preço de mercado
- **KaBuM:** primeira consulta de cada produto. Se houver pelo menos uma cotação aprovada pelo comparador, ela é a base e o Google não é consultado.
- **Google Shopping no Chrome (Playwright):** alternativa automática somente quando a KaBuM não trouxer nenhuma cotação compatível, inclusive em caso de falha de acesso. Resultados de outro modelo não contam como produto encontrado.
  - Abre diretamente a página Shopping (`udm=28`) com a consulta do produto e reutiliza o mesmo perfil, cookies e janela visível que passaram pelos testes.
  - Lê até 30 anúncios da mesma página, sem recarregar entre anúncios e sem abrir cada loja. O comparador filtra os anúncios antes de calcular a referência.
  - Reaproveita resultados válidos por consulta durante 30 dias. Consultas repetidas não abrem novas buscas quando há cache válido.
  - Mantém intervalo mínimo de 60 segundos, até 5 tentativas por hora, sem limite diário. Esses limites estão em `pricing.google_browser` no `config/settings.yaml`; são separados do antigo limite da segunda opinião no painel.
  - Em bloqueio/CAPTCHA, pausa novas consultas e deixa o Chrome aberto para o usuário resolver manualmente. Não resolve verificações automaticamente. A liberação é reconhecida quando a mesma página volta a mostrar produtos legíveis; os limites já usados continuam valendo.
- **HasData/segunda opinião paga:** desativada nesta configuração. Uma oferta rentável na KaBuM não dispara uma consulta adicional no Google.

A regra vale para ofertas recebidas e buscas dos produtos acompanhados, inclusive produtos fora do catálogo. Os filtros existentes continuam descartando outros modelos, capacidades, acessórios, usados identificáveis e preços incompatíveis. Cartões sem preço total ou loja identificável são descartados. Frete e links de loja ausentes permanecem desconhecidos.

Uma única cotação compatível da KaBuM dispensa a busca alternativa, mas o mínimo de preços necessário para declarar rentabilidade continua sendo o definido nas regras (`min_quotes`, atualmente 3). Se faltar informação, o sistema registra a confiança correspondente; não inventa preços de referência.

### 4. Conta da revenda
Para cada canal de venda:

```
preço de venda  = referência de mercado − 5% (para vender rápido)
lucro           = venda − comissão − frete − imposto (6%) − embalagem − reserva p/ devolução (3%) − custo da compra
ROI             = lucro ÷ custo da compra
```

A **referência de mercado** é conservadora: nunca passa de 10% acima do anúncio mais barato do mesmo modelo, e, quando vem de produtos equivalentes, usa o quartil inferior em vez da mediana.

### 5. Decisão
Uma oferta é **rentável** quando, por unidade:
- **Lucro ≥ R$ 25 + 5% do custo** (R$ 100 → R$ 30 · R$ 1.000 → R$ 75 · R$ 3.000 → R$ 175)
- **ROI ≥ 15%** (12% se a liquidez for alta, 25% se for baixa)
- Pelo menos **3 preços** comparáveis
- Preço **não** abaixo de 40% do mercado (isso costuma ser golpe, assinatura ou erro → vai como "confira manualmente")

Tudo isso é ajustável no painel, inclusive por **grupo de produtos** que você cria (ex.: "Fones e carregadores" com uma margem, "Computador e monitor" com outra).

---

## Onde você vê tudo

| Lugar | O que mostra |
|---|---|
| **Telegram (seu bot de alertas)** | Alerta de cada oportunidade: preço, lucro, ROI, melhor canal, cupom e links |
| **Painel — aba Radar** (hospedagem estática própria) | Todas as ofertas analisadas, comparação de preços, projeção de lucro do mês |
| **Painel — Minhas vendas** | O que você comprou, por quanto vendeu, lucro real × esperado × potencial, dinheiro parado em estoque |
| **Painel — Ajustes** | Lucro mínimo, ROI, limite da 2ª opinião e grupos de produtos |

A **projeção do mês** usa as últimas 24 h × 30 até ter 7 dias de dados, depois a última semana × 30/7, e com 30 dias mostra o mês real.

---

## Por que "Ver na OLX" e "Ver no Marketplace"

### A necessidade
As lojas (KaBuM, Mercado Livre, Magalu…) mostram **por quanto o produto é vendido novo**. Mas boa parte da revenda acontece em **OLX e Facebook Marketplace**, onde:
- o comprador compara com outros anúncios **de pessoas**, não de lojas;
- os preços costumam ser **menores** que nas lojas (concorrência com usados e com quem também revende);
- o preço que dá para pedir **varia por região**.

Então, antes de comprar, vale responder: **"Por quanto as pessoas estão anunciando isto lá?"** Se na OLX o mesmo SSD está saindo por R$ 600 e o radar estimou a venda a R$ 1.100 (preço de loja), o lucro real na venda direta é bem menor do que o calculado.

### Por que não é automático
- **OLX:** bloqueia qualquer acesso automatizado (Cloudflare) e não tem API pública de busca.
- **Facebook Marketplace:** a única API de leitura é restrita a pesquisadores acadêmicos; automatizar exigiria usar a sua conta do Facebook, que seria bloqueada.
- Burlar esses bloqueios vai contra os termos dos sites e quebra com frequência, por isso o radar não faz isso.

### Como funciona agora
Em cada alerta do Telegram e em cada oferta do painel há dois botões:

- **Ver na OLX** → abre a busca da OLX (Brasil todo) já preenchida com o termo curto do produto, ex. `SSD NVMe 1TB`
- **Ver no Marketplace** → abre a busca do Facebook Marketplace com o mesmo termo (usa a região da sua conta)

É um clique, custa R$ 0 e mostra na hora os preços gerais praticados (novos e usados), para você conferir se a revenda direta compensa.

> Possível próximo passo (gratuito com limite): automatizar a consulta da OLX por um serviço de terceiros (Apify, plano grátis ~US$ 5/mês), só para as ofertas rentáveis, como a 2ª opinião.

---

## Peças do sistema

| Peça | Onde roda | Função |
|---|---|---|
| Monitor (`main.py run`) | Seu PC (execução local ou agendada no sistema operacional) | Lê os grupos, analisa, grava e avisa |
| Banco (Supabase) | Nuvem | Ofertas, análises, preços, compras, regras e cache de buscas. Só a sua conta de admin acessa |
| Painel | Hospedagem estática com login (ex.: Netlify, Vercel ou local) | Visualização, vendas e ajustes |
| Bot do Telegram | Telegram | Envia os alertas só para você |

**Custos atuais:** R$ 0 em serviços de busca na configuração ativa (KaBuM e navegador local; HasData desativada), com Supabase e hospedagem estática nos planos gratuitos. O PC precisa ficar ligado com internet para o monitor funcionar.

## Limites conhecidos
- Lucro é **estimativa**: depende das taxas configuradas e do preço de mercado estar certo. Confira antes de comprar.
- Se o navegador estiver bloqueado, atingir os limites ou não encontrar cartões legíveis, o produto pode ficar sem referência de mercado. As referências válidas já salvas continuam disponíveis; sem cache válido, a KaBuM é consultada primeiro.
- Produtos "irmãos" muito parecidos ainda podem se misturar às vezes; ofertas com **confiança baixa** merecem olhar manual.
- Cupons e promoções relâmpago podem acabar antes de você abrir o link.


## Referência de mercado por 30 dias

Antes de consultar as fontes, o monitor procura cotações salvas para a mesma busca de produto. Revalida o modelo e a compatibilidade com a promoção atual. Com referência compatível de menos de 30 dias, recalcula a oportunidade usando o novo preço do grupo, sem pesquisar novamente na KaBuM ou no Google. O prazo conta da coleta original e não é renovado a cada leitura.

Ao vencer, atualiza somente quando surgir outra oferta daquele produto. Sem referência válida, mantém KaBuM primeiro e Google Shopping apenas sem cotação KaBuM compatível. Erros e resultados vazios não criam referência mensal. O teto de compra de R$ 150 por unidade continua aplicado antes de consultar o cache ou as fontes.

A validade é configurável em `pricing.reference_cache_days: 30`; o cache persistente do navegador usa `pricing.google_browser.cache_hours: 720`. A integração ReefAPI/Zoom/Buscapé está configurada como última alternativa, após Chrome e HasData.


## Alternativas em sequência e referências usadas

Fluxo ativo: preço de compra até R$ 150/un → referência salva por 30 dias → KaBuM → Chrome → HasData → ReefAPI (Zoom/Buscapé). Só passa à próxima fonte quando a anterior não fornece cotação compatível, falha, está bloqueada ou atinge cota. Uma cotação compatível encerra a sequência, mesmo quando não atinge o mínimo de cotações necessário para classificar a oportunidade como rentável. As fontes pagas não servem mais como segunda opinião automática de uma referência já encontrada.

A HasData usa o limite existente `rules.segunda_opiniao_limite_diario`, agora apresentado como “HasData: máximo de consultas/dia” no código do painel. Zero pula a HasData. Esse limite não se aplica ao Chrome nem à ReefAPI. Tentativas são registradas para respeitar o teto mesmo se houver erro ou nenhum resultado.

A ReefAPI exige `REEF_API_KEY` no `.env` local. Usa uma pesquisa para encontrar o produto e uma consulta para listar ofertas. Guarda o identificador para atualizar ofertas sem pesquisar o nome novamente. O cache mensal evita até essa chamada enquanto as cotações forem válidas. Não consulta Zoom e Buscapé separadamente para duplicar resultados.

`pricing.reef.accept_used: true` preserva referências explicitamente informadas como usadas. Elas aparecem com `[usado]` no título, mantêm a condição estruturada no cache e entram na comparação de preços. Os filtros de modelo, acessórios e defeitos continuam ativos. A contagem por condição fica no perfil da análise. A resposta de ofertas também fica salva no banco para não perder dados coletados.
