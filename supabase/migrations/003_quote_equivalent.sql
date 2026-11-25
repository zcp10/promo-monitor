-- Preço aceito por especificação (produto equivalente de outra marca/modelo), não o mesmo modelo.
alter table public.market_quotes add column if not exists equivalent boolean not null default false;
