-- Buscas pagas (HasData/Google Shopping) guardadas para reaproveitar.
-- A chave depende do grupo: SSD reaproveita por especificação (qualquer marca),
-- placa de vídeo por modelo, celular/fone por marca + modelo.
create table if not exists public.market_cache (
  key        text primary key,
  query      text not null,
  provider   text not null,
  quotes     jsonb not null default '[]'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists market_cache_created_idx on public.market_cache (provider, created_at desc);
alter table public.market_cache enable row level security;
drop policy if exists "admin read cache" on public.market_cache;
create policy "admin read cache" on public.market_cache for select to authenticated
  using ((select private.is_admin()));

-- Limite diário de buscas pagas, editável no painel (aba Ajustes)
update public.app_settings
   set rules = rules || jsonb_build_object('segunda_opiniao_limite_diario', 10)
 where id = 1 and not (rules ? 'segunda_opiniao_limite_diario');
