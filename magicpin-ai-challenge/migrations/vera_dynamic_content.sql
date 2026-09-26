-- Vera dynamic-content schema: versioned contexts, conversations, messages.
-- Additive + idempotent. Server writes via DATABASE_URL (owner role, bypasses RLS);
-- client-side access is governed by the auth.uid() policies below.

create table if not exists public.vera_contexts (
  id bigint generated always as identity primary key,
  scope text not null check (scope in ('category','merchant','customer','trigger')),
  context_id text not null,
  version int not null check (version >= 1),
  payload jsonb not null default '{}'::jsonb,
  owner_user_id uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (scope, context_id)
);
create index if not exists vera_contexts_owner_idx on public.vera_contexts (owner_user_id);

create table if not exists public.vera_conversations (
  id text primary key,
  merchant_id text not null,
  customer_id text,
  route text,
  state text not null default 'INITIATED',
  owner_user_id uuid references auth.users(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists vera_conversations_owner_idx on public.vera_conversations (owner_user_id);
create index if not exists vera_conversations_merchant_idx on public.vera_conversations (merchant_id, updated_at desc);

create table if not exists public.vera_messages (
  id bigint generated always as identity primary key,
  conversation_id text not null references public.vera_conversations(id) on delete cascade,
  sender text not null,
  body text not null,
  action jsonb,
  created_at timestamptz not null default now()
);
create index if not exists vera_messages_conversation_idx on public.vera_messages (conversation_id, created_at);

alter table public.vera_contexts enable row level security;
alter table public.vera_conversations enable row level security;
alter table public.vera_messages enable row level security;

drop policy if exists "vera_contexts_owner_all" on public.vera_contexts;
create policy "vera_contexts_owner_all" on public.vera_contexts
  for all using (auth.uid() = owner_user_id) with check (auth.uid() = owner_user_id);

drop policy if exists "vera_conversations_owner_all" on public.vera_conversations;
create policy "vera_conversations_owner_all" on public.vera_conversations
  for all using (auth.uid() = owner_user_id) with check (auth.uid() = owner_user_id);

drop policy if exists "vera_messages_owner_all" on public.vera_messages;
create policy "vera_messages_owner_all" on public.vera_messages
  for all using (
    exists (
      select 1 from public.vera_conversations c
      where c.id = vera_messages.conversation_id and c.owner_user_id = auth.uid()
    )
  ) with check (
    exists (
      select 1 from public.vera_conversations c
      where c.id = vera_messages.conversation_id and c.owner_user_id = auth.uid()
    )
  );

grant select, insert, update, delete on public.vera_contexts to authenticated;
grant select, insert, update, delete on public.vera_conversations to authenticated;
grant select, insert, update, delete on public.vera_messages to authenticated;
