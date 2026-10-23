from datetime import date,datetime,timedelta,UTC
import base64
from urllib.parse import urlparse,parse_qs
import pytest
from sqlalchemy import select,func
from app.models import *
from app.domain import plus_month,grant,local_today,active_access,lesson_access,process_payment
from app.security import encode
from app import getcourse


def callback(client,order,**changes):
    data={"order_id":order.provider_id,"order_number":"VP-"+order.id,"email":"different@example.com","status":"payed","amount":order.amount/100,"currency":"RUB",**changes}
    return client.post('/api/getcourse/webhook',json=data,headers={"X-Webhook-Secret":"unit-test-callback-secret"})


def make_order(db,user,product='base'):
    order=Order(user_id=user.id,product=product,amount=99000 if product=='base' else 199000,provider_id=uid())
    db.add(order);db.commit();return order


@pytest.mark.parametrize('start,end',[(date(2026,1,31),date(2026,2,28)),(date(2028,1,31),date(2028,2,29)),(date(2026,12,31),date(2027,1,31))])
def test_calendar_month(start,end): assert plus_month(start)==end


def test_registration_retains_session(client,account):
    user,headers=account
    assert client.get('/api/me',headers=headers).status_code==200
    refreshed=client.post('/api/auth/refresh')
    assert refreshed.status_code==200
    assert 'HttpOnly' in refreshed.headers['set-cookie']
    assert user.email not in refreshed.cookies.get('refresh_token','')


def test_logout_revokes_access_token(client,account):
    _,headers=account
    assert client.post('/api/auth/logout').status_code==200
    assert client.get('/api/me',headers=headers).status_code==401


def test_wrong_password(client,account):
    assert client.post('/api/auth/login',json={'email':'anna@example.com','password':'wrong'}).status_code==401


def test_csrf_is_rejected(client,account):
    assert client.post('/api/auth/logout',headers={'Origin':'https://attacker.example'}).status_code==403


def test_unverified_email_does_not_block_checkout(client,db,account,content,monkeypatch):
    user,headers=account
    assert not user.verified
    monkeypatch.setattr('app.main.create_checkout',lambda *_:('gc-test','number','https://merchant.getcourse.ru/pay'))
    response=client.post('/api/checkout',json={'product':'base'},headers=headers)
    assert response.status_code==200
    assert db.scalar(select(Order).where(Order.user_id==user.id)).status=='pending'


def test_unpublished_cycle_cannot_be_sold(client,account):
    _,headers=account
    response=client.post('/api/checkout',json={'product':'base'},headers=headers)
    assert response.status_code==409


def test_unauthenticated_webhook_never_grants(client,db,account):
    user,_=account; order=make_order(db,user)
    response=client.post('/api/getcourse/webhook',json={'order_id':order.provider_id,'status':'payed','amount':990})
    assert response.status_code==403
    assert db.scalar(select(func.count()).select_from(Cycle))==0


def test_duplicate_payment_grants_once_and_ignores_changed_email(client,db,account,content):
    user,_=account;order=make_order(db,user)
    assert callback(client,order).json()['status']=='paid'
    assert callback(client,order).json()['status']=='paid'
    assert db.scalar(select(func.count()).select_from(Cycle))==1
    assert db.scalar(select(Cycle)).user_id==user.id
    assert db.scalar(select(func.count()).select_from(Outbox).where(Outbox.kind=='welcome'))==1


@pytest.mark.parametrize('changes',[{'amount':9.9},{'currency':'USD'},{'status':'part_payed'}])
def test_incorrect_payment_cannot_grant(client,db,account,changes):
    user,_=account;order=make_order(db,user)
    callback(client,order,**changes)
    assert db.scalar(select(func.count()).select_from(Cycle))==0


def test_unknown_payment_goes_to_review(client,db,account):
    response=client.post('/api/getcourse/webhook',json={'order_id':'unknown','email':'anna@example.com','status':'payed','amount':990},headers={'X-Webhook-Secret':'unit-test-callback-secret'})
    assert response.json()['status']=='review'
    assert db.scalar(select(func.count()).select_from(Cycle))==0
    assert db.scalar(select(PaymentEvent)).note


def test_refund_revokes_access_and_cannot_regrant(client,db,account,content):
    user,_=account;order=make_order(db,user)
    callback(client,order);assert active_access(db,user)
    callback(client,order,status='refunded');assert not active_access(db,user)
    callback(client,order);assert not active_access(db,user)


def test_early_and_late_renewal(db,account):
    user,_=account
    first=grant(db,user,'base',today=date(2026,10,1));db.flush()
    second=grant(db,user,'base',today=date(2026,10,15));db.flush()
    assert first.end==second.start==date(2026,10,22)
    assert second.access_end==date(2026,11,22)
    third=grant(db,user,'base',today=date(2026,12,1))
    assert third.start==date(2026,12,1)


def test_timezone_midnight(account):
    user,_=account;user.timezone='Asia/Vladivostok'
    assert local_today(user,datetime(2026,10,21,14,1,tzinfo=UTC))==date(2026,10,22)
    user.timezone='Europe/Moscow'
    assert local_today(user,datetime(2026,10,21,14,1,tzinfo=UTC))==date(2026,10,21)


def test_day22_archive_and_month_expiration(db,account,content):
    user,_=account;c=grant(db,user,'base',today=date(2026,10,1));db.flush()
    lesson=next(l for l in content if l.cycle_number==1 and l.day==21 and l.kind=='short')
    assert lesson_access(db,user,lesson,today=date(2026,10,23))
    assert not lesson_access(db,user,lesson,today=date(2026,11,1))


def test_full_requires_base_even_if_addon_is_still_active(db,access,content):
    user,_,cycle,_=access
    grant(db,user,'full');db.flush()
    lesson=next(l for l in content if l.cycle_number==1 and l.day==1 and l.kind=='full')
    assert lesson_access(db,user,lesson)
    cycle.revoked=True;db.flush()
    assert not lesson_access(db,user,lesson)


def test_future_lesson_is_locked(client,access,content):
    _,headers,_,_=access
    future=next(l for l in content if l.cycle_number==1 and l.day==2 and l.kind=='short')
    assert client.post('/api/watch/start',json={'lesson_id':future.id},headers=headers).status_code==403


def test_progress_accumulates_across_runs_and_deduplicates(client,db,access):
    _,headers,_,lesson=access
    for run in range(2):
        r=client.post('/api/watch/start',json={'lesson_id':lesson.id},headers=headers)
        assert r.status_code==200,r.text
        wid=r.json()['watch_id']
        for i in range(5):
            watch=db.get(WatchSession,wid);watch.last_seen=now()-timedelta(seconds=20);db.commit()
            r=client.post(f'/api/watch/{wid}/chunk',json={'sequence':i,'seconds':20},headers=headers)
            assert r.status_code==200,r.text
            before=r.json()['seconds']
            duplicate=client.post(f'/api/watch/{wid}/chunk',json={'sequence':i,'seconds':20},headers=headers)
            assert duplicate.json()['seconds']==before
    progress=db.scalar(select(Progress).where(Progress.lesson_id==lesson.id))
    assert progress.seconds==200 and progress.completed_at
    assert db.scalar(select(func.count()).select_from(Event).where(Event.type=='completed'))==1


def test_instant_heartbeat_cannot_forge_three_minutes(client,db,access):
    _,headers,_,lesson=access
    wid=client.post('/api/watch/start',json={'lesson_id':lesson.id},headers=headers).json()['watch_id']
    for i in range(12):client.post(f'/api/watch/{wid}/chunk',json={'sequence':i,'seconds':20},headers=headers)
    progress=db.scalar(select(Progress))
    assert progress.seconds<10 and not progress.completed_at


def test_another_device_stops_current_counter(client,db,access):
    _,headers,_,lesson=access
    first=client.post('/api/watch/start',json={'lesson_id':lesson.id},headers=headers).json()['watch_id']
    client.post('/api/watch/start',json={'lesson_id':lesson.id},headers=headers)
    assert client.post(f'/api/watch/{first}/chunk',json={'sequence':0,'seconds':10},headers=headers).status_code==409


def test_full_trial_is_once_for_the_account(client,db,access,content):
    user,headers,cycle,_=access
    first=next(l for l in content if l.cycle_number==1 and l.day==1 and l.kind=='full')
    second=next(l for l in content if l.cycle_number==1 and l.day==2 and l.kind=='full')
    assert client.post('/api/watch/start',json={'lesson_id':first.id},headers=headers).status_code==200
    cycle.start-=timedelta(days=1);db.commit()
    assert client.post('/api/watch/start',json={'lesson_id':second.id},headers=headers).status_code==403
    assert client.post('/api/watch/start',json={'lesson_id':first.id},headers=headers).status_code==200


def test_playback_token_cannot_authenticate_api_and_logout_revokes_drm(client,access):
    _,headers,_,lesson=access
    data=client.post('/api/watch/start',json={'lesson_id':lesson.id},headers=headers).json()
    token=parse_qs(urlparse(data['url']).query)['drmauthtoken'][0]
    assert client.get('/api/me',headers={'Authorization':'Bearer '+token}).status_code==401
    basic='Basic '+base64.b64encode(b'drm:unit-test-drm-password').decode()
    assert client.post('/api/kinescope/authorize',json={'id':lesson.video_id,'token':token},headers={'Authorization':basic}).status_code==200
    client.post('/api/auth/logout')
    assert client.post('/api/kinescope/authorize',json={'id':lesson.video_id,'token':token},headers={'Authorization':basic}).status_code==403


def test_user_cannot_access_admin_or_another_order(client,db,account):
    user,headers=account
    other=User(email='other@example.com',name='Other',password_hash='no');db.add(other);db.flush()
    order=make_order(db,other)
    assert client.get('/api/admin/stats',headers=headers).status_code==403
    assert client.get('/api/orders/'+order.id,headers=headers).status_code==404


def test_password_reset_is_single_use_and_revokes_sessions(client,db,account):
    _,headers=account
    client.post('/api/auth/forgot',json={'email':'anna@example.com'})
    row=db.scalar(select(Outbox).where(Outbox.kind=='reset'));token=row.payload['token']
    assert client.post('/api/auth/reset',json={'token':token,'password':'brand-new-password-333'}).status_code==200
    assert client.post('/api/auth/reset',json={'token':token,'password':'another-password-444'}).status_code==400
    assert client.get('/api/me',headers=headers).status_code==401


def test_export_uses_order_id_instead_of_display_number():
    data=getcourse.normalize_export({'ID заказа':'123','Номер':'999','Статус':'Завершен','Стоимость, RUB':'990.00','Email':'a@example.com'})
    assert data['order_id']=='123' and data['amount']=='990.00'


def test_checkout_retry_uses_same_external_number_without_resetting_paid_state(db,account,monkeypatch):
    user,_=account;order=make_order(db,user);captured=[]
    from app.config import settings
    monkeypatch.setattr(settings(),'gc_base_offer','daily-21')
    monkeypatch.setattr(settings(),'gc_account_url','https://merchant.getcourse.ru')
    def fake(path,**kwargs):
        import json
        captured.append(json.loads(base64.b64decode(kwargs['data']['params'])))
        return {'result':{'success':True,'deal_id':'123','payment_link':'https://merchant.getcourse.ru/pay'}}
    monkeypatch.setattr(getcourse,'api_request',fake)
    getcourse.create_checkout(order,user);getcourse.create_checkout(order,user)
    assert captured[0]['deal']['deal_number']==captured[1]['deal']['deal_number']=='VP-'+order.id
    assert 'deal_status' not in captured[0]['deal'] and 'deal_is_paid' not in captured[0]['deal']


def test_retention_excludes_young_cohorts_and_test_accounts(client,db,access):
    user,headers,cycle,_=access
    admin=User(email='admin@example.com',name='Admin',password_hash='no',role='admin');db.add(admin);db.commit()
    from app.security import new_session
    from fastapi import Response
    response=Response();token=new_session(db,admin,response)['access_token']
    result=client.get('/api/admin/stats',headers={'Authorization':'Bearer '+token}).json()
    assert result['retention'][0]['eligible']==1
    assert result['retention'][6]['eligible']==0
    user.is_test=True;db.commit()
    result=client.get('/api/admin/stats',headers={'Authorization':'Bearer '+token}).json()
    assert result['retention'][0]['eligible']==0
