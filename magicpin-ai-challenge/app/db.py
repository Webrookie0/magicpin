"""Postgres persistence (Verdent Supabase) for dynamic content.

Writes are best-effort: the API must keep answering even if the database is
unreachable, and everything degrades to a no-op when DATABASE_URL is absent.
"""
import json
import os
from datetime import datetime, timezone

_DATABASE_URL = os.environ.get("DATABASE_URL", "")


def enabled() -> bool:
    return bool(_DATABASE_URL or os.environ.get("DATABASE_URL", ""))


def _execute(sql: str, params: tuple = (), fetch: bool = False):
    import psycopg

    url = _DATABASE_URL or os.environ["DATABASE_URL"]
    with psycopg.connect(url, connect_timeout=8) as c:
        with c.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall() if fetch else None


def _best_effort(label: str, fn, *args, **kwargs):
    if not enabled():
        return None
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # never let persistence break the API
        print(json.dumps({"event": "db_write_failed", "op": label, "error": str(exc)[:200]}))
        return None


def save_context(scope: str, context_id: str, version: int, payload: dict, owner: str | None):
    def _run():
        _execute(
            """
            insert into public.vera_contexts (scope, context_id, version, payload, owner_user_id)
            values (%s, %s, %s, %s, %s)
            on conflict (scope, context_id) do update
              set version = excluded.version,
                  payload = excluded.payload,
                  owner_user_id = coalesce(excluded.owner_user_id, vera_contexts.owner_user_id),
                  updated_at = now()
            """,
            (scope, context_id, version, json.dumps(payload), owner),
        )
    return _best_effort("save_context", _run)


def save_conversation(conv_id: str, merchant_id: str, customer_id: str | None,
                      route: str, state: str, owner: str | None):
    def _run():
        _execute(
            """
            insert into public.vera_conversations
              (id, merchant_id, customer_id, route, state, owner_user_id)
            values (%s, %s, %s, %s, %s, %s)
            on conflict (id) do update
              set state = excluded.state,
                  route = excluded.route,
                  updated_at = now()
            """,
            (conv_id, merchant_id, customer_id, route, state, owner),
        )
    return _best_effort("save_conversation", _run)


def save_message(conv_id: str, sender: str, body: str, action: dict | None):
    def _run():
        _execute(
            """
            insert into public.vera_messages (conversation_id, sender, body, action)
            values (%s, %s, %s, %s)
            """,
            (conv_id, sender, body, json.dumps(action) if action else None),
        )
    return _best_effort("save_message", _run)


def list_conversations(limit: int = 50):
    def _run():
        rows = _execute(
            """
            select c.id, c.merchant_id, c.customer_id, c.route, c.state,
                   c.created_at, c.updated_at,
                   (select count(*) from public.vera_messages m where m.conversation_id = c.id)
             from public.vera_conversations c
             order by c.updated_at desc
             limit %s
            """,
            (limit,),
            fetch=True,
        )
        out = []
        for r in rows or []:
            out.append({
                "conversation_id": r[0], "merchant_id": r[1], "customer_id": r[2],
                "route": r[3], "state": r[4],
                "created_at": r[5].isoformat() if isinstance(r[5], datetime) else None,
                "updated_at": r[6].isoformat() if isinstance(r[6], datetime) else None,
                "message_count": r[7],
            })
        return out
    return _best_effort("list_conversations", _run)


def conversation_messages(conv_id: str, limit: int = 100):
    def _run():
        rows = _execute(
            """
            select sender, body, action, created_at
              from public.vera_messages
             where conversation_id = %s
             order by created_at asc, id asc
             limit %s
            """,
            (conv_id, limit),
            fetch=True,
        )
        return [
            {
                "sender": r[0], "body": r[1],
                "action": json.loads(r[2]) if r[2] else None,
                "created_at": r[3].isoformat() if isinstance(r[3], datetime) else None,
            }
            for r in rows or []
        ]
    return _best_effort("conversation_messages", _run)


def health() -> dict:
    if not enabled():
        return {"status": "disabled", "detail": "DATABASE_URL not configured"}
    try:
        _execute("select 1", fetch=True)
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)[:200]}
