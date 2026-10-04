from dataclasses import replace
import json

import httpx
import pytest

from mtg_bot.line_adapter import to_line_messages
from mtg_bot.models import CardResponse
from mtg_bot.parser import parse_queries
from mtg_bot.renderer.cards import CardRenderer
from mtg_bot.resolver import Resolver
from mtg_bot.scryfall import ScryfallClient, decode_card


@pytest.mark.parametrize('language,name', [
    ('zht','反擊咒語'), ('zhs','反击咒语'), ('ja','対抗呪文'), ('ko','주문 무효화'),
    ('fr','Contresort'), ('de','Gegenzauber'), ('es','Contrahechizo'), ('it','Contromagia'),
    ('pt','Contramágica'), ('ru','Контрзаклинание'), ('en','Counterspell'),
])
async def test_scryfall_names_forwarded_without_translation(language,name):
    calls=[]
    def mock(req):
        calls.append(req)
        assert req.url.params['fuzzy']==name
        return httpx.Response(200,json={
            'id':'id','name':'Counterspell','printed_name':name,'lang':language,'layout':'normal',
            'scryfall_uri':'https://scryfall.com/card/test?utm_source=api',
            'oracle_text':'Counter target spell.','printed_text':'official printed text',
            'printed_type_line':'official printed type',
            'image_uris':{'normal':f'https://cards.scryfall.io/{language}.jpg'},
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        resolver=Resolver(ScryfallClient(http,user_agent='test'))
        try:
            results=await resolver.resolve_many(parse_queries(f'[[{name}]] [[!{name}]]').queries)
            responses=await CardRenderer().render(results)
            data=json.dumps(to_line_messages(responses)[0].to_dict(),ensure_ascii=False)
            assert name in data and 'Counterspell' in data
            assert f'/{language}.jpg' in data
            assert 'official printed text' in data and 'Counter target spell.' in data
            assert 'utm_source' not in data and len(calls)==1
        finally: await resolver.close()


@pytest.mark.parametrize('missing', ['image','text','name','all'])
def test_partial_language_data(card,missing):
    data={'id':'a','name':'Counterspell','layout':'normal','lang':'ja',
          'scryfall_uri':card.scryfall_uri,'oracle_text':'Counter target spell.'}
    if missing not in ('image','all'): data['image_uris']={'normal':card.image_url}
    if missing not in ('text','all'): data['printed_text']='呪文１つを対象とし、それを打ち消す。'
    if missing not in ('name','all'): data['printed_name']='対抗呪文'
    decoded=decode_card(data)
    output=json.dumps(to_line_messages([CardResponse(decoded)])[0].to_dict(),ensure_ascii=False)
    assert 'Counterspell' in output and 'Counter target spell.' in output
    if missing in ('image','all'): assert '卡圖未提供' in output and 'hero' not in output
    if missing in ('text','all'): assert '未提供此語言牌面文字' in output
    if missing in ('name','all'): assert '対抗呪文' not in output


def test_localized_double_faces(card):
    decoded=decode_card({'id':'a','name':'Front // Back','layout':'transform','lang':'ja',
        'scryfall_uri':card.scryfall_uri,'card_faces':[
            {'name':'Front','printed_name':'表','oracle_text':'Front rules','printed_text':'表テキスト',
             'image_uris':{'normal':'https://cards.scryfall.io/front-ja.jpg'}},
            {'name':'Back','printed_name':'裏','oracle_text':'Back rules','printed_text':'裏テキスト',
             'image_uris':{'normal':'https://cards.scryfall.io/back-ja.jpg'}}]})
    for mode in ['text','image']:
        output=json.dumps(to_line_messages([CardResponse(decoded,mode)])[0].to_dict(),ensure_ascii=False)
        assert '表' in output and '裏' in output
        assert 'front-ja.jpg' in output and 'back-ja.jpg' in output
        if mode=='text': assert '表テキスト' in output and '裏テキスト' in output


async def test_localized_set_and_collector_and_cache_separation():
    calls=[]
    def mock(req):
        calls.append(req)
        if req.url.path=='/cards/named':
            lang='ja' if req.url.params.get('fuzzy')=='対抗呪文' else 'en'
        else: lang='ja' if req.url.path.endswith('/ja') else 'en'
        return httpx.Response(200,json={'id':lang,'name':'Counterspell','lang':lang,'layout':'normal',
           'printed_name':'対抗呪文' if lang=='ja' else '',
           'scryfall_uri':f'https://scryfall.com/card/7ed/67/{lang}'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        resolver=Resolver(ScryfallClient(http,user_agent='test'))
        try:
            queries=parse_queries('[[対抗呪文|7ED|67]] [[Counterspell|7ED|67]]').queries
            results=await resolver.resolve_many(queries)
            assert [r.card.lang for r in results]==['ja','en']
            assert any(r.url.path=='/cards/7ed/67/ja' for r in calls)
            count=len(calls)
            await resolver.resolve_many(queries)
            assert len(calls)==count
        finally: await resolver.close()


async def test_missing_language_print_keeps_exact_print_and_notes():
    def mock(req):
        if req.url.path.endswith('/ja'): return httpx.Response(404,json={})
        return httpx.Response(200,json={'id':'a','name':'Counterspell','lang':'ja' if req.url.path.endswith('/named') else 'en',
                                      'layout':'normal','scryfall_uri':'https://scryfall.com/card/7ed/67'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        client=ScryfallClient(http,user_agent='test')
        card=await client.get_card_by_collector('7ed','67','対抗呪文')
        assert card.lang=='en' and '未收錄' in card.language_note
        assert card.scryfall_uri.endswith('/7ed/67')


async def test_unknown_name_does_not_override_collector_identity():
    def mock(req):
        if req.url.path.endswith('/named'): return httpx.Response(404,json={})
        return httpx.Response(200,json={'id':'a','name':'Jace','layout':'normal','scryfall_uri':'https://scryfall.com/card/wwk/31'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        assert (await ScryfallClient(http,user_agent='test').get_card_by_collector('wwk','31','wrongname')).name=='Jace'


def test_unknown_language_code_displays_as_provided(card):
    card=replace(card,lang='future-language',printed_name='Provided name')
    data=json.dumps(to_line_messages([CardResponse(card)])[0].to_dict())
    assert 'future-language' in data and 'Provided name' in data
