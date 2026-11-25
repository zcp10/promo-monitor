-- Promo Monitor: esquema inicial
-- Rode no SQL Editor do Supabase (ou via CLI de migrações).

create extension if not exists pgcrypto;

-- Ofertas encontradas (posts do Telegram ou checagens da watchlist)
create table if not exists public.offers (
  id            uuid primary key default gen_random_uuid(),
  source        text not null check (source in ('telegram','watchlist','manual')),
  chat_id       bigint,
  chat_title    text,
  message_id    bigint,
  raw_text      text,
  title         text not null,
  price         numeric(12,2) not null,
  old_price     numeric(12,2),
  coupon        text,
  store         text,
  url           text,
  shipping_cost numeric(12,2),
  posted_at     timestamptz,
  created_at    timestamptz not null default now(),
  unique (chat_id, message_id)
);
create index if not exists offers_created_idx on public.offers (created_at desc);

-- Preços de mercado coletados para comparação
create table if not exists public.market_quotes (
  id          uuid primary key default gen_random_uuid(),
  offer_id    uuid not null references public.offers(id) on delete cascade,
  provider    text not null,              -- google_shopping | mercadolivre | scrape
  store       text,
  title       text,
  price       numeric(12,2) not null,
  shipping    numeric(12,2),
  url         text,
  match_score numeric(5,3),
  accepted    boolean not null default true,
  created_at  timestamptz not null default now()
);
create index if not exists quotes_offer_idx on public.market_quotes (offer_id);

-- Resultado da análise de revenda
create table if not exists public.analyses (
  id             uuid primary key default gen_random_uuid(),
  offer_id       uuid not null references public.offers(id) on delete cascade,
  market_median  numeric(12,2),
  market_min     numeric(12,2),
  n_quotes       int not null default 0,
  best_channel   text,
  sale_price     numeric(12,2),
  total_cost     numeric(12,2),
  net_revenue    numeric(12,2),
  profit         numeric(12,2),
  roi            numeric(8,4),
  is_profitable  boolean not null default false,
  confidence     text,                    -- alta | media | baixa
  details        jsonb,
  created_at     timestamptz not null default now()
);
create index if not exists analyses_offer_idx on public.analyses (offer_id);
create index if not exists analyses_profitable_idx on public.analyses (is_profitable, created_at desc);

-- Produtos fixos monitorados (ex.: "avise quando o RTX 4060 da Kabum cair abaixo de X")
create table if not exists public.watchlist (
  id              uuid primary key default gen_random_uuid(),
  name            text not null,
  query           text not null,          -- termo de busca usado nas fontes
  url             text,                   -- página específica (opcional)
  target_price    numeric(12,2),          -- avisa se ficar abaixo deste preço
  min_profit      numeric(12,2),          -- ou se o lucro estimado passar disto
  active          boolean not null default true,
  last_price      numeric(12,2),
  last_store      text,
  last_checked_at timestamptz,
  created_at      timestamptz not null default now()
);

create table if not exists public.price_history (
  id         uuid primary key default gen_random_uuid(),
  watch_id   uuid not null references public.watchlist(id) on delete cascade,
  price      numeric(12,2) not null,
  store      text,
  url        text,
  checked_at timestamptz not null default now()
);
create index if not exists history_watch_idx on public.price_history (watch_id, checked_at desc);

-- Registro dos alertas enviados (evita repetição)
create table if not exists public.alerts (
  id          uuid primary key default gen_random_uuid(),
  analysis_id uuid references public.analyses(id) on delete cascade,
  watch_id    uuid references public.watchlist(id) on delete cascade,
  channel     text not null default 'telegram',
  message     text,
  sent_at     timestamptz not null default now()
);
create index if not exists alerts_analysis_idx on public.alerts (analysis_id);
create index if not exists alerts_watch_idx on public.alerts (watch_id, sent_at desc);

-- Visão usada pelo painel: oferta + última análise
create or replace view public.v_opportunities
with (security_invoker = true) as
select distinct on (o.id)
  o.id as offer_id, o.source, o.chat_title, o.title, o.price, o.old_price,
  o.coupon, o.store, o.url, o.created_at,
  a.id as analysis_id, a.market_median, a.market_min, a.n_quotes,
  a.best_channel, a.sale_price, a.total_cost, a.profit, a.roi,
  a.is_profitable, a.confidence, a.details
from public.offers o
left join public.analyses a on a.offer_id = o.id
order by o.id, a.created_at desc;

-- Segurança: o script Python usa a service_role (ignora RLS).
-- O painel usa a chave pública + login. "Estar logado" NÃO basta: por padrão o
-- Supabase deixa qualquer pessoa criar conta. Só quem estiver em app_admins acessa.
-- Depois de criar seu usuário em Authentication, rode:
--   insert into public.app_admins (user_id) select id from auth.users where email = 'SEU@EMAIL';
-- E desative "Allow new users to sign up" em Authentication > Sign In / Providers.
create table if not exists public.app_admins (
  user_id uuid primary key references auth.users(id) on delete cascade
);
alter table public.app_admins enable row level security;

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.app_admins where user_id = (select auth.uid()))
$$;
revoke all on function public.is_admin() from public, anon;
grant execute on function public.is_admin() to authenticated;

alter table public.offers        enable row level security;
alter table public.market_quotes enable row level security;
alter table public.analyses      enable row level security;
alter table public.watchlist     enable row level security;
alter table public.price_history enable row level security;
alter table public.alerts        enable row level security;

do $$
declare t text;
begin
  foreach t in array array['offers','market_quotes','analyses','price_history','alerts'] loop
    execute format('drop policy if exists "auth read %1$s" on public.%1$I', t);
    execute format('create policy "auth read %1$s" on public.%1$I for select to authenticated using ((select public.is_admin()))', t);
  end loop;
end $$;

drop policy if exists "auth all watchlist" on public.watchlist;
create policy "auth all watchlist" on public.watchlist
  for all to authenticated using ((select public.is_admin())) with check ((select public.is_admin()));

-- Atualização ao vivo no painel quando uma nova análise entra
do $$ begin
  alter publication supabase_realtime add table public.analyses;
exception when others then null; end $$;
