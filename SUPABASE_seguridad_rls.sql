-- SEGURIDAD: protege todas las tablas de la base (Supabase) contra el acceso por la API pública.
-- Corre esto UNA vez en Supabase (SQL Editor). Es seguro repetirlo.
--
-- Contexto: el backend crea sus tablas en el esquema `public` y se conecta directo con el usuario `postgres`, que se salta
-- RLS. Supabase publica `public` por una API abierta (con la llave anon); sin RLS cualquiera con la URL del proyecto podría
-- leer, editar o borrar esas tablas (incluida `users`, con correos, celulares y contraseñas cifradas). Con RLS activado y sin
-- políticas, la API pública no puede tocar nada; el backend y las copias con la llave service_role siguen funcionando.

-- 1) Activar RLS en todas las tablas de public
do $$
declare r record;
begin
  for r in select tablename from pg_tables where schemaname = 'public' loop
    execute format('alter table public.%I enable row level security', r.tablename);
  end loop;
end $$;

-- 2) Quitar todo permiso de la API pública sobre esas tablas
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;

-- 3) Que toda tabla nueva que cree el backend (access_codes, feedback_limite, foto_lugar, limit_bonus…) nazca con RLS activado
create or replace function public.rls_auto_enable() returns event_trigger
language plpgsql security definer set search_path = pg_catalog as $$
declare cmd record;
begin
  for cmd in select * from pg_event_trigger_ddl_commands()
    where command_tag in ('CREATE TABLE','CREATE TABLE AS','SELECT INTO') and object_type in ('table','partitioned table')
  loop
    if cmd.schema_name = 'public' then
      execute format('alter table %s enable row level security', cmd.object_identity);
    end if;
  end loop;
end $$;

drop event trigger if exists ensure_rls;
create event trigger ensure_rls on ddl_command_end
  when tag in ('CREATE TABLE','CREATE TABLE AS','SELECT INTO')
  execute function public.rls_auto_enable();

-- Comprobación (debe devolver 0 filas): tablas de public sin RLS
-- select tablename from pg_tables where schemaname = 'public' and not rowsecurity;
