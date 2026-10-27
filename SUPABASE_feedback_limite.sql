-- Corre esto una vez en Supabase (SQL Editor). Feedback que los usuarios dejan al llegar a un límite
-- (me gusta / no me gusta + por qué) a cambio de una ampliación gratis. La app escribe con la service_role key.
create table if not exists public.feedback_limite (
  id          text primary key,
  user_id     text,
  trip_id     text,
  recurso     text,
  gusta       boolean,
  motivo      text,
  day         text,
  ampliado    boolean,
  created_at  timestamptz not null default now()
);

alter table public.feedback_limite enable row level security;
