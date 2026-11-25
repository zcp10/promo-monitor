-- Ajustes pelo painel, grupos de produtos com regras próprias e registro de compras/vendas.
-- Tudo só para admin (private.is_admin); o monitor Python usa a service_role.

-- Regras globais editáveis no site (uma linha só). O monitor relê a cada 5 min.
create table if not exists public.app_settings (
  id         int primary key default 1 check (id = 1),
  rules      jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
insert into public.app_settings (id, rules) values (1, jsonb_build_object(
  'min_profit_base', 25, 'min_profit_pct', 0.05, 'min_roi', 0.15, 'min_quotes', 3,
  'por_liquidez', jsonb_build_object('alta', jsonb_build_object('min_roi', 0.12),
                                     'media', jsonb_build_object(),
                                     'baixa', jsonb_build_object('min_roi', 0.25))))
on conflict (id) do nothing;

-- Categorias do catálogo (config/categories.yaml), sincronizadas pelo monitor ao iniciar.
create table if not exists public.catalog_categories (
  key       text primary key,
  nome      text not null,
  liquidez  text,
  comparar  text,
  synced_at timestamptz not null default now()
);

-- Grupos criados pelo usuário: "Fones e carregadores", "Computador e monitor"...
-- Campos nulos herdam a regra global/liquidez.
create table if not exists public.rule_groups (
  id              uuid primary key default gen_random_uuid(),
  name            text not null check (length(name) between 1 and 60),
  categories      text[] not null default '{}',
  min_profit_base numeric(10,2) check (min_profit_base >= 0),
  min_profit_pct  numeric(6,4)  check (min_profit_pct between 0 and 1),
  min_roi         numeric(6,4)  check (min_roi between 0 and 5),
  created_at      timestamptz not null default now()
);

-- O que foi de fato comprado e por quanto foi vendido.
create table if not exists public.purchases (
  id              uuid primary key default gen_random_uuid(),
  offer_id        uuid references public.offers(id) on delete set null,
  title           text not null check (length(title) between 1 and 300),
  quantity        int not null default 1 check (quantity between 1 and 1000),
  unit_cost       numeric(12,2) not null check (unit_cost >= 0),
  expected_profit numeric(12,2),            -- lucro estimado pelo radar (total) na hora da compra
  status          text not null default 'comprado' check (status in ('comprado','vendido','cancelado')),
  sold_qty        int not null default 0 check (sold_qty >= 0),
  sold_total      numeric(12,2) not null default 0 check (sold_total >= 0),  -- quanto recebeu bruto
  fees            numeric(12,2) not null default 0 check (fees >= 0),        -- taxas + frete + embalagem reais
  bought_at       timestamptz not null default now(),
  sold_at         timestamptz,
  notes           text check (length(notes) <= 500)
);
create index if not exists purchases_bought_idx on public.purchases (bought_at desc);
create index if not exists purchases_offer_idx on public.purchases (offer_id);

alter table public.app_settings       enable row level security;
alter table public.catalog_categories enable row level security;
alter table public.rule_groups        enable row level security;
alter table public.purchases          enable row level security;

drop policy if exists "admin settings" on public.app_settings;
create policy "admin settings" on public.app_settings for all to authenticated
  using ((select private.is_admin())) with check ((select private.is_admin()));
drop policy if exists "admin read categories" on public.catalog_categories;
create policy "admin read categories" on public.catalog_categories for select to authenticated
  using ((select private.is_admin()));
drop policy if exists "admin groups" on public.rule_groups;
create policy "admin groups" on public.rule_groups for all to authenticated
  using ((select private.is_admin())) with check ((select private.is_admin()));
drop policy if exists "admin purchases" on public.purchases;
create policy "admin purchases" on public.purchases for all to authenticated
  using ((select private.is_admin())) with check ((select private.is_admin()));

-- Visão do painel: acrescenta a quantidade do post
create or replace view public.v_opportunities
with (security_invoker = true) as
select distinct on (o.id)
  o.id as offer_id, o.source, o.chat_title, o.title, o.price, o.old_price,
  o.coupon, o.store, o.url, o.created_at,
  a.id as analysis_id, a.market_median, a.market_min, a.n_quotes,
  a.best_channel, a.sale_price, a.total_cost, a.profit, a.roi,
  a.is_profitable, a.confidence, a.details, o.quantity
from public.offers o
left join public.analyses a on a.offer_id = o.id
order by o.id, a.created_at desc;
