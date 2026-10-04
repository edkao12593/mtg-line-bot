import asyncio
import base64
import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient
from mtg_bot.app import Settings, create_app, worker
from mtg_bot.line_adapter import to_line_messages
from mtg_bot.models import ImageResponse, TextResponse
from mtg_bot.storage import Inbox, InboxFull, LocalImageStore


def post(client,body,signature=True):
    raw = json.dumps(body,ensure_ascii=False).encode()
    sig = base64.b64encode(hmac.new(b'secret',raw,hashlib.sha256).digest()).decode() if signature else 'bad'
    return client.post('/webhook',content=raw,headers={'x-line-signature':sig})


def event(text='[[Sol Ring]]',id='event1'):
    return {'type':'message','webhookEventId':id,'replyToken':'token','source':{'type':'group','groupId':'group'},'message':{'type':'text','text':text}}


def test_signature_silence_multiple_and_dedup(tmp_path):
    app = create_app(Settings('secret','token','https://bot.test','test',tmp_path),start_workers=False)
    with TestClient(app) as client:
        assert post(client,{'events':[event()]},False).status_code == 400
        assert app.state.inbox.claim() is None
        assert post(client,{'events':[]}).status_code == 200
        assert post(client,{'events':[event('普通聊天')]}).status_code == 200
        assert app.state.inbox.claim() is None
        body = {'events':[event('[[Sol Ring]] [[Mana Vault]]'),event('[[A]]','event2')]}
        assert post(client,body).status_code == 200
        assert post(client,body).status_code == 200
        assert app.state.inbox.claim()[2] == '[[Sol Ring]] [[Mana Vault]]'
        assert app.state.inbox.claim()[0] == 'event2'
        assert app.state.inbox.claim() is None


@pytest.mark.parametrize('e', [{'type':'join'}, {'type':'follow'}, {'type':'message','message':{'type':'image'}}, dict(event(),mode='standby')])
def test_ignored_events(tmp_path,e):
    app = create_app(Settings('secret','token','https://bot.test','test',tmp_path),start_workers=False)
    with TestClient(app) as client:
        assert post(client,{'events':[e]}).status_code == 200
        assert app.state.inbox.claim() is None


def test_line_mapping_and_limit():
    messages = to_line_messages([ImageResponse('https://b.test/a.jpg','https://b.test/p.jpg'),TextResponse('oops')])
    assert messages[0].original_content_url == 'https://b.test/a.jpg'
    assert messages[1].text == 'oops'
    with pytest.raises(ValueError): to_line_messages([TextResponse('a')] * 6)


def test_queue_atomic_capacity_recovery_and_expiry(tmp_path):
    now = [100]
    path = tmp_path/'inbox.db'
    inbox = Inbox(path,capacity=1,clock=lambda:now[0])
    with pytest.raises(InboxFull): inbox.enqueue([{'id':'a','token':'t','text':'x'},{'id':'b','token':'t','text':'x'}])
    assert inbox.claim() is None
    inbox.enqueue([{'id':'a','token':'t','text':'x'}])
    assert inbox.claim()[0] == 'a'
    inbox.close()
    inbox = Inbox(path,clock=lambda:now[0])
    assert inbox.claim() is None  # uncertain processing rows not resent
    inbox.enqueue([{'id':'a','token':'t','text':'x'}])
    assert inbox.claim() is None
    inbox.enqueue([{'id':'b','token':'t','text':'x'}])
    now[0] += 41
    inbox.enqueue([])
    assert inbox.claim() is None
    inbox.close()


async def test_worker_sends_once_and_scrubs_sensitive_fields(tmp_path):
    inbox = Inbox(tmp_path/'inbox.db')
    inbox.enqueue([{'id':'a','token':'t','text':'[[A]]'}])
    sent = asyncio.Event()
    class Service:
        async def handle_text(self,text): return [TextResponse('a')]
    class Sender:
        async def reply(self,token,responses): sent.set()
    task = asyncio.create_task(worker(inbox,Service(),Sender()))
    await asyncio.wait_for(sent.wait(),2)
    task.cancel()
    await asyncio.gather(task,return_exceptions=True)
    assert inbox.db.execute('SELECT token,text,state FROM events').fetchone() == ('','','sent')
    inbox.close()


async def test_store_and_served_image(tmp_path,jpeg):
    store = LocalImageStore(tmp_path/'images','https://bot.test')
    response = await store.publish(jpeg,jpeg)
    assert response.original_url.startswith('https://bot.test/images/')
    app = create_app(Settings('secret','token','https://bot.test','test',tmp_path),start_workers=False)
    with TestClient(app) as client:
        image = client.get(response.original_url.replace('https://bot.test',''))
        assert image.status_code == 200 and image.content == jpeg
        assert client.get('/images/nope').status_code == 404


async def test_full_signed_webhook_core_pipeline(tmp_path,jpeg):
    import httpx
    from mtg_bot.scryfall import ScryfallClient
    from mtg_bot.resolver import Resolver
    from mtg_bot.renderer.image import ImageRenderer
    from mtg_bot.service import LookupService
    calls, replies = [], []
    def upstream(request):
        calls.append(str(request.url))
        if request.url.host == 'api.scryfall.com':
            name = request.url.params['fuzzy']
            if name == 'bad': return httpx.Response(404,json={})
            return httpx.Response(200,json={'id':name,'name':name,'layout':'normal','scryfall_uri':'https://scryfall.com/card/test','image_uris':{'normal':'https://cards.scryfall.io/' + name.replace(' ','-') + '.jpg'}})
        return httpx.Response(200,content=jpeg)
    class Sender:
        async def reply(self,token,responses): replies.append((token,responses))
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as http:
        resolver = Resolver(ScryfallClient(http,user_agent='test/contact'))
        service = LookupService(resolver,ImageRenderer(http,LocalImageStore(tmp_path/'images','https://bot.test')))
        app = create_app(Settings('secret','token','https://bot.test','test',tmp_path),service=service,sender=Sender())
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://bot.test') as client:
                body = {'events':[event('[[Sol Ring]] [[Mana Vault]] [[bad]] [[ sol ring ]]')]}
                raw = json.dumps(body).encode()
                sig = base64.b64encode(hmac.new(b'secret',raw,hashlib.sha256).digest()).decode()
                for _ in range(2):
                    assert (await client.post('/webhook',content=raw,headers={'x-line-signature':sig})).status_code == 200
                for _ in range(100):
                    if replies: break
                    await asyncio.sleep(.01)
                assert len(replies) == 1
                token,responses = replies[0]
                assert token == 'token' and len(responses) == 2
                assert 'bad' in responses[1].text
                image = await client.get(responses[0].original_url)
                assert image.status_code == 200 and image.headers['content-type'] == 'image/jpeg'
                assert len([url for url in calls if 'api.scryfall.com' in url]) == 3
        await resolver.close()


async def test_reply_failure_is_terminal_not_retried(tmp_path):
    inbox = Inbox(tmp_path/'inbox.db')
    inbox.enqueue([{'id':'a','token':'t','text':'[[A]]'}])
    attempted = asyncio.Event()
    class Service:
        async def handle_text(self,text): return [TextResponse('a')]
    class Sender:
        async def reply(self,token,responses):
            attempted.set()
            raise RuntimeError('network unknown')
    task = asyncio.create_task(worker(inbox,Service(),Sender()))
    await asyncio.wait_for(attempted.wait(),2)
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.gather(task,return_exceptions=True)
    assert inbox.db.execute('SELECT state FROM events').fetchone()[0] == 'uncertain'
    inbox.enqueue([{'id':'a','token':'t','text':'[[A]]'}])
    assert inbox.claim() is None
    inbox.close()
