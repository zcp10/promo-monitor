# Piloto Google Shopping — Radar

O Radar usa referência salva por 30 dias → KaBuM → Google/Scrapling isolado → HasData → ReefAPI. Uma fonte que retorna preços compatíveis encerra a sequência. O teto de R$ 150 é sobre o preço por unidade da promoção; não sobre o preço de referência.

Uma consulta Google por vez, intervalo mínimo de 60 segundos após terminar, até 10 tentativas por hora, sem teto diário. Ao falhar, a pausa aumenta para no mínimo 60/120/300/1800 segundos e respeita Retry-After. A tentativa seguinte depende de chegar outra oferta elegível; não há repetição automática da mesma busca durante a pausa. O Radar pode usar as alternativas pagas dentro das cotas já configuradas.

## O que fazer

1. Opcionalmente, configure uma tarefa agendada no sistema operacional para abrir o Docker e iniciar o coletor automaticamente. Mantenha o computador acordado e internet disponível.
2. Deixe o Radar receber ofertas normalmente por alguns dias. Não é preciso abrir Google, conectar uma conta pessoal ou cadastrar outra chave.
3. Se encontrar modelo errado, kit comparado como unidade, preço incoerente ou falta de referência, guarde o nome da oferta e o horário para conferir nos logs.

O navegador isolado usa uma tela virtual; não aparece como janela no seu Chrome. Você pode exportar o último print com o comando Screenshot abaixo. CAPTCHA não é resolvido automaticamente: a busca pausa e o Radar segue para outra fonte. Se o Docker estiver fechado, o Radar também segue pelas alternativas.

## Comandos PowerShell

```powershell
& '.\browser\pilot\pilot.ps1' -Action Status
& '.\browser\pilot\pilot.ps1' -Action Screenshot
```

Screenshot salva `latest.png` nesta pasta. Status mostra contagens de sucesso/bloqueio/erro, última consulta e próximo horário permitido (Unix UTC). Para iniciar os contêineres novamente, use `-Action Start`; para pará-los, `-Action Stop`. O Docker reinicia automaticamente os contêineres quando volta a funcionar, se não foram parados manualmente.

O log operacional é `logs\radar.log`. Procure `Google Scrapling` e `Fonte google_browser`: cotações coletadas não significam cotações compatíveis; o segundo registro informa quantas passaram pelo filtro.

## Isolamento e limites do teste

Dependências ficam na imagem auditada, perfil e estado ficam apenas no volume Docker `radar-google-profile`. Sem pasta pessoal montada, credenciais do Radar, portas publicadas ou socket Docker dentro do coletor. Saída por proxy limitado a domínios Google e endereços públicos. Não utiliza perfis ou cookies do Chrome pessoal. Arquivos de diagnóstico são locais; os destinos permitidos continuam recebendo as requisições normais da navegação.

Os testes prévios bem sucedidos não determinam uma cota oficial do Google nem garantem acesso permanente. O tratamento de bloqueio foi validado em testes simulados; a recuperação real ainda precisa ser observada. OLX continua sendo um experimento separado, ainda sem integração de produção neste piloto.

Para reverter, coloque `pricing.google_browser.enabled: false` em `config/settings.yaml` e reinicie o processo do monitor. Isso mantém KaBuM e as alternativas já existentes.

## Ajuste em 28/10/2026

As regras de busca ficam em `app_settings.rules.google_search`: máximo por hora (10), intervalo mínimo (60s), catálogo obrigatório, lista de categorias permitidas e chamadas ignoradas. Atualizações do banco entram em até 5 minutos. A seleção cobre fones, informática, eletrônicos, games e pequenos eletrodomésticos/ferramentas. É uma seleção inicial de revenda por categoria; não é um ranking comprovado de cada modelo.

Foram preparados os controles correspondentes em `dashboard/dist/`. A publicação da versão nova do painel é separada da ativação do monitor.

## Inicialização automática (opcional)

O script `browser/pilot/start_on_login.py` pode ser registrado opcionalmente no agendador de tarefas do sistema operacional. Ele aguarda o Docker ficar pronto e inicia o coletor sem executar consultas, registrando diagnósticos em `logs/coletor-startup.log` sem exigir senha ou login automático.

