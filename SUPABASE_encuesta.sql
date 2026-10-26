-- Corre esto una vez en Supabase (SQL Editor). La app escribe aquí con la service_role key (salta RLS);
-- sin políticas, con la anon key nadie puede leer ni escribir esta tabla.
create table if not exists public.encuesta_cierre (
  trip_id        text primary key,
  user_id        text,
  ciudad         text,
  nota_destino   smallint check (nota_destino between 1 and 5),
  gusto_destino  text,
  mejora_destino text,
  nota_app       smallint check (nota_app between 1 and 5),
  gusto_app      text,
  mejora_app     text,
  feature        text,
  created_at     timestamptz not null default now()
);

alter table public.encuesta_cierre enable row level security;
