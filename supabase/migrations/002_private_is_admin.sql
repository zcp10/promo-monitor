-- Move is_admin() para um schema fora da API REST (advisor 0029).
-- app_admins continua sem policies de propósito: só a service_role lê/escreve.

create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated;

create or replace function private.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.app_admins where user_id = (select auth.uid()))
$$;
revoke all on function private.is_admin() from public, anon;
grant execute on function private.is_admin() to authenticated;

do $$
declare t text;
begin
  foreach t in array array['offers','market_quotes','analyses','price_history','alerts'] loop
    execute format('drop policy if exists "auth read %1$s" on public.%1$I', t);
    execute format('create policy "auth read %1$s" on public.%1$I for select to authenticated using ((select private.is_admin()))', t);
  end loop;
end $$;

drop policy if exists "auth all watchlist" on public.watchlist;
create policy "auth all watchlist" on public.watchlist
  for all to authenticated using ((select private.is_admin())) with check ((select private.is_admin()));

drop function if exists public.is_admin();
