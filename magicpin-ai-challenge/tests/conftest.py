import json
from copy import deepcopy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import bot

ROOT = Path(__file__).resolve().parents[1] / 'dataset' / 'expanded'
DATA = {}
for scope, folder, key in [('category','categories','slug'), ('merchant','merchants','merchant_id'), ('customer','customers','customer_id'), ('trigger','triggers','id')]:
    DATA[scope] = {obj[key]: obj for path in sorted((ROOT / folder).glob('*.json')) for obj in [json.loads(path.read_text())]}


@pytest.fixture
def data():
    return deepcopy(DATA)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv('VERA_USE_LLM', 'false')
    monkeypatch.delenv('VERA_API_TOKEN', raising=False)
    monkeypatch.delenv('VERA_REQUIRE_AUTH', raising=False)
    with TestClient(bot.app) as c:
        c.post('/v1/teardown')
        yield c
        c.post('/v1/teardown')


def push(client, scope, payload, version=1):
    key = {'category':'slug', 'merchant':'merchant_id', 'customer':'customer_id', 'trigger':'id'}[scope]
    return client.post('/v1/context', json={'scope':scope, 'context_id':payload[key], 'version':version, 'payload':payload})


def start(client, data, prefix='trg_001', now='2026-04-26T10:00:00Z'):
    trigger = next(t for tid, t in data['trigger'].items() if tid.startswith(prefix))
    merchant = data['merchant'][trigger['merchant_id']]
    for scope, value in [('category',data['category'][merchant['category_slug']]), ('merchant',merchant)]:
        response = push(client, scope, value)
        assert response.status_code in (200,409), response.text
    if trigger.get('customer_id'):
        response = push(client, 'customer', data['customer'][trigger['customer_id']])
        assert response.status_code in (200,409), response.text
    response = push(client, 'trigger', trigger)
    assert response.status_code in (200,409), response.text
    response = client.post('/v1/tick', json={'now':now,'available_triggers':[trigger['id']]})
    assert response.status_code == 200, response.text
    return response.json()['actions'][0]


def reply(client, action, message, turn=2, now='2026-04-26T10:01:00Z', **extra):
    payload = {'conversation_id':action['conversation_id'], 'merchant_id':action['merchant_id'],
               'customer_id':action['customer_id'], 'from_role':'customer' if action['customer_id'] else 'merchant',
               'message':message, 'turn_number':turn, 'received_at':now}
    payload.update(extra)
    return client.post('/v1/reply',json=payload)
