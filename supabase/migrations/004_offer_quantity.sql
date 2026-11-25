-- Unidades vendidas juntas no post ("5 fones por R$ 50" -> 5). A análise é por unidade.
alter table public.offers add column if not exists quantity int not null default 1 check (quantity between 1 and 200);
