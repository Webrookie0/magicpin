from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest

import bot
from app.stores import ContextStore, ConversationStore
from conftest import push, start, reply


def test_full_warmup_and_teardown(client, data):
    for scope in ('category','merchant','customer'):
        for obj in data[scope].values():
            assert push(client, scope, obj).status_code == 200
    assert client.get('/v1/healthz').json()['contexts_loaded'] == {'category':5,'merchant':50,'customer':200,'trigger':0}
    start(client,data)
    assert bot.outreach.audit
    assert client.post('/v1/teardown').json() == {'wiped':True}
    assert not bot.outreach.audit and not bot.convs._convs
    assert all(v == 0 for v in client.get('/v1/healthz').json()['contexts_loaded'].values())


def test_versions_replace_atomically_and_conflict_shape(client,data):
    merchant = next(iter(data['merchant'].values()))
    assert push(client,'merchant',merchant,2).status_code == 200
    for version in (1,2):
        response=push(client,'merchant',merchant,version)
        assert response.status_code == 409
        assert response.json() == {'accepted':False,'reason':'stale_version','current_version':2}
    merchant.pop('offers')
    assert push(client,'merchant',merchant,3).status_code == 200
    assert 'offers' not in bot.contexts.get('merchant',merchant['merchant_id']).payload


def test_store_defensive_copy_and_concurrent_version_writes():
    store=ContextStore()
    payload={'name':'before'}
    record,_=store.put('category','x',1,payload)
    payload['name']='outside mutation'
    record.payload['name']='returned mutation'
    assert store.get('category','x').payload['name']=='before'
    with ThreadPoolExecutor() as executor:
        list(executor.map(lambda v:store.put('category','x',v,{'version':v}),range(2,101)))
    assert store.get('category','x').version==100


@pytest.mark.parametrize('field,value', [('scope','invalid'),('version',True),('version','1'),('version',0),('context_id',' '),('context_id','wrong')])
def test_invalid_envelope_is_400(client,data,field,value):
    category=data['category']['dentists']
    body={'scope':'category','context_id':'dentists','version':1,'payload':category}
    body[field]=value
    assert client.post('/v1/context',json=body).status_code==400


@pytest.mark.parametrize('scope,path,value',[
    ('merchant','identity',None),('merchant','offers','oops'),('merchant','conversation_history',[1]),
    ('category','voice',[]),('trigger','scope',[]),('trigger','urgency','urgent'),('trigger','expires_at','yesterday'),
    ('customer','preferences',[]),('category','digest',[None]),
])
def test_invalid_context_shapes_do_not_crash(client,data,scope,path,value):
    payload=next(iter(data[scope].values()))
    payload[path]=value
    assert push(client,scope,payload).status_code==400


@pytest.mark.parametrize('timestamp',['yesterday','2026-04-26T10:00:00','2026-04-26',''])
def test_tick_requires_aware_clock(client,timestamp):
    assert client.post('/v1/tick',json={'now':timestamp}).status_code==400


def test_context_limit(client):
    response=client.post('/v1/context',json={'scope':'category','context_id':'x','version':1,'payload':{'slug':'x','data':'a'*512001}})
    assert response.status_code==413


def test_metadata_stable(client):
    first=client.get('/v1/metadata').json()
    assert first==client.get('/v1/metadata').json()


def test_tick_no_trigger_expiry_and_dedup(client,data):
    assert client.post('/v1/tick',json={'now':'2026-04-26T10:00:00Z'}).json()=={'actions':[]}
    action=start(client,data)
    assert action['template_params']==[action['body']]
    for now in ('2026-04-26T10:00:00Z','2026-04-26T11:00:00Z'):
        assert client.post('/v1/tick',json={'now':now,'available_triggers':[action['trigger_id']]}).json()=={'actions':[]}
    client.post('/v1/teardown')
    trigger=data['trigger'][action['trigger_id']]
    trigger['expires_at']='2026-04-26T10:00:00Z'
    merchant=data['merchant'][trigger['merchant_id']]
    for scope,obj in [('category',data['category']['dentists']),('merchant',merchant),('trigger',trigger)]:
        push(client,scope,obj)
    assert client.post('/v1/tick',json={'now':trigger['expires_at'],'available_triggers':[trigger['id']]}).json()=={'actions':[]}


def test_template_window_uses_only_real_recipient_reply(client,data):
    action=start(client,data,'trg_013')
    assert action['template_name'] is None
    assert action['template_params']==[]
    customer=start(client,data,'trg_003')
    assert customer['template_name'] is not None


def test_payload_id_layout_and_conflict(client,data):
    t=next(t for tid,t in data['trigger'].items() if tid.startswith('trg_001'))
    m=data['merchant'][t['merchant_id']]
    push(client,'merchant',m)
    push(client,'category',data['category']['dentists'])
    t['payload']['merchant_id']=t.pop('merchant_id')
    assert push(client,'trigger',t).status_code==200
    assert len(client.post('/v1/tick',json={'now':'2026-04-26T10:00:00Z','available_triggers':[t['id']]}).json()['actions'])==1
    t['merchant_id']='different'
    assert push(client,'trigger',t,2).status_code==400


def test_shared_suppression_key_scoped_per_recipient(client,data):
    action=start(client,data)
    original=data['trigger'][action['trigger_id']]
    other=deepcopy(original)
    other['id']='shared-event-other-merchant'
    merchant=next(m for mid,m in data['merchant'].items() if mid.startswith('m_002'))
    other['merchant_id']=merchant['merchant_id']
    push(client,'merchant',merchant)
    push(client,'trigger',other)
    actions=client.post('/v1/tick',json={'now':'2026-04-26T10:00:00Z','available_triggers':[other['id']]}).json()['actions']
    assert len(actions)==1 and actions[0]['conversation_id']!=action['conversation_id']


def test_updated_context_reaches_next_send_and_audit(client,data):
    action=start(client,data)
    category=data['category']['dentists']
    category['digest'][0]['title']='Updated research finding'
    category['digest'][0]['summary']='New summary from supplied evidence.'
    push(client,'category',category,2)
    response=reply(client,action,'Yes please send it').json()
    assert 'New summary from supplied evidence.' in response['body']
    assert any(r['scope']=='category' and r['version']==2 for r in bot.outreach.audit[-1]['contexts'])


def test_missing_category_does_not_send(client,data):
    action=start(client,data)
    bot.contexts.wipe()
    push(client,'merchant',data['merchant'][action['merchant_id']])
    t=data['trigger'][action['trigger_id']]
    t['id']='new';t['suppression_key']='new'
    push(client,'trigger',t)
    assert client.post('/v1/tick',json={'now':'2026-04-26T11:00:00Z','available_triggers':['new']}).json()=={'actions':[]}


def test_urgency_order_and_customer_independent_of_merchant(client,data):
    tids=[]
    for prefix in ('trg_001','trg_002','trg_003'):
        t=next(t for tid,t in data['trigger'].items() if tid.startswith(prefix))
        tids.append(t['id'])
        m=data['merchant'][t['merchant_id']]
        push(client,'category',data['category'][m['category_slug']]);push(client,'merchant',m)
        if t['customer_id']:
            push(client,'customer',data['customer'][t['customer_id']])
        push(client,'trigger',t)
    actions=client.post('/v1/tick',json={'now':'2026-04-26T10:00:00Z','available_triggers':tids}).json()['actions']
    assert [a['trigger_id'] for a in actions]==[tids[1],tids[2]]


def test_trigger_without_suppression_key_still_deduplicates(client,data):
    t=next(t for tid,t in data['trigger'].items() if tid.startswith('trg_001'))
    t['suppression_key']=''
    action=start(client,data)
    assert action['suppression_key']==''
    assert client.post('/v1/tick',json={'now':'2026-04-26T11:00:00Z','available_triggers':[t['id']]}).json()=={'actions':[]}


def test_historical_optout_is_respected(client,data):
    t=next(t for tid,t in data['trigger'].items() if tid.startswith('trg_001'))
    m=data['merchant'][t['merchant_id']]
    m['conversation_history'].append({'from':'merchant','body':'Stop messaging me','ts':'2026-04-25T10:00:00Z'})
    push(client,'category',data['category']['dentists']);push(client,'merchant',m);push(client,'trigger',t)
    assert client.post('/v1/tick',json={'now':'2026-04-26T10:00:00Z','available_triggers':[t['id']]}).json()=={'actions':[]}


def test_twenty_action_cap(client,data):
    for cat in data['category'].values():
        push(client,'category',cat)
    tids=[]
    for i,m in enumerate(data['merchant'].values()):
        push(client,'merchant',m)
        t={'id':f'cap-{i}','kind':'new_event','scope':'merchant','merchant_id':m['merchant_id'],
           'payload':{'headline':'Updated business information'},'urgency':2,'suppression_key':f'cap-{i}'}
        push(client,'trigger',t);tids.append(t['id'])
    assert len(client.post('/v1/tick',json={'now':'2026-04-26T10:00:00Z','available_triggers':tids}).json()['actions'])==20
