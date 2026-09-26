from copy import deepcopy

import pytest

import bot
from app.conversation import classify, normalize
from app.routing import consent_allows, get_policy
from app.timeutils import parse_time
from conftest import push, start, reply


def test_unknown_purpose_and_partial_scope_fail_closed(data):
    c=next(iter(data['customer'].values()))
    assert not consent_allows(get_policy('unknown_customer_event'),c)
    for scopes in (['rec'],['recall_alerts'],['delivery_notifications'],['promotional_offers']):
        c['consent']['scope']=scopes
        assert not consent_allows(get_policy('recall_due'),c)


@pytest.mark.parametrize('where,key,value',[
    ('consent','opted_in_at',None),('consent','revoked_at','2026-04-20'),
    ('consent','opted_out_at','2026-04-20'),('consent','active',False),
    ('consent','expires_at','2026-04-25'),('consent','opted_in_at','2027-01-01'),
    ('preferences','reminder_opt_in',False),('preferences','channel','sms'),
])
def test_consent_revocation_and_channel(data,where,key,value):
    c=next(iter(data['customer'].values()))
    c[where][key]=value
    assert not consent_allows(get_policy('recall_due'),c,parse_time('2026-04-26T10:00:00Z'))


def test_customer_optout_does_not_suppress_merchant(client,data):
    customer=start(client,data,'trg_003')
    assert reply(client,customer,'STOP').json()['action']=='end'
    assert (customer['merchant_id'],customer['customer_id']) in bot.outreach.suppressed_recipients
    merchant=start(client,data)
    assert merchant['send_as']=='vera'
    t=deepcopy(data['trigger'][customer['trigger_id']]);t['id']='recall-again';t['suppression_key']='new-recall'
    push(client,'trigger',t)
    assert client.post('/v1/tick',json={'now':'2026-04-26T11:00:00Z','available_triggers':[t['id']]}).json()=={'actions':[]}
    assert reply(client,customer,'YES',3).json()['action']=='end'


def test_customer_ownership_checked_initial_and_followup(client,data):
    action=start(client,data,'trg_003')
    customer=data['customer'][action['customer_id']]
    customer['merchant_id']='unrelated'
    push(client,'customer',customer,2)
    assert reply(client,action,'YES').json()['action']=='end'


def test_revoked_consent_checked_before_reply(client,data):
    action=start(client,data,'trg_003')
    customer=data['customer'][action['customer_id']]
    customer['consent']['scope']=[]
    push(client,'customer',customer,2)
    assert reply(client,action,'YES').json()['action']=='end'


def test_reply_identity_and_role_binding(client,data):
    action=start(client,data)
    for extra in ({'merchant_id':'wrong'},{'customer_id':'wrong'},{'from_role':'customer'}):
        assert reply(client,action,'STOP',**extra).status_code==400
    assert not bot.outreach.suppressed_recipients
    assert client.post('/v1/reply',json={'conversation_id':'unknown','message':'yes'}).status_code==404


def test_auto_replies_and_ended_terminal(client,data):
    action=start(client,data)
    moves=[]
    for turn in range(2,6):
        moves.append(reply(client,action,'Thank you for contacting us! Our team will respond shortly.',turn).json()['action'])
    assert moves==['send','wait','end','end']
    assert reply(client,action,'yes',6).json()['action']=='end'


def test_repeated_unrecognized_auto_reply_detected(client,data):
    action=start(client,data)
    message='आपका संदेश मिल गया है टीम संपर्क करेगी'
    assert normalize(message)
    moves=[reply(client,action,message,turn).json()['action'] for turn in range(2,8)]
    assert moves[-1]=='end'
    assert moves.count('send')<=1


@pytest.mark.parametrize('message,expected',[
    ('no thanks','rejection'),('I do not want to book','rejection'),('not now, okay','defer'),
    ('How do I book?','question'),('Can you confirm the price?','question'),
    ('Yes please send the abstract','intent'),("Ok let's do it. What's next?",'intent'),
    ('Okay, can you file my GST?','off_topic'),('stop messaging me','opt_out'),
])
def test_classifier_precedence(message,expected):
    assert classify(message)==expected


def test_intent_produces_real_draft_not_execution_claim(client,data):
    action=start(client,data)
    response=reply(client,action,"Ok let's do it. What's next?").json()
    assert response['action']=='send'
    assert 'Source summary:' in response['body'] and 'Customer note draft:' in response['body']
    for claim in ('Sending the abstract now','2 pages','tomorrow 10am','published','Booking you in'):
        assert claim not in response['body']


def test_customer_slot_choice_never_confirms_booking(client,data):
    action=start(client,data,'trg_003')
    response=reply(client,action,'1').json()
    assert response['action']=='send'
    assert 'requested time' in response['body']
    assert 'still needs to confirm' in response['body']


def test_reply_retry_idempotency_and_conflicting_turn(client,data):
    action=start(client,data)
    first=reply(client,action,'YES')
    count=len(bot.convs.get(action['conversation_id']).turns)
    assert first.json()==reply(client,action,'YES').json()
    assert len(bot.convs.get(action['conversation_id']).turns)==count
    assert reply(client,action,'No').status_code==409


def test_wait_blocks_new_triggers_but_human_can_resume(client,data):
    action=start(client,data)
    assert reply(client,action,'Maybe later').json()['action']=='wait'
    trigger=deepcopy(data['trigger'][action['trigger_id']]);trigger['id']='new';trigger['suppression_key']='new'
    push(client,'trigger',trigger)
    assert client.post('/v1/tick',json={'now':'2026-04-26T11:00:00Z','available_triggers':['new']}).json()=={'actions':[]}
    assert reply(client,action,'Go ahead',3,now='2026-04-26T11:01:00Z').json()['action']=='send'


def test_repetition_and_optout(client,data):
    action=start(client,data)
    first=reply(client,action,'YES').json()
    again=reply(client,action,'YES',3).json()
    assert first['action']=='send' and again['action']=='wait'
    assert reply(client,action,'Stop messaging me',4).json()['action']=='end'
    assert reply(client,action,'Can you help with GST?',5).json()['action']=='end'
