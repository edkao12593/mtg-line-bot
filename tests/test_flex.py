from dataclasses import replace
import json

import httpx
import pytest

from mtg_bot.line_adapter import to_line_messages
from mtg_bot.line_flex import flex_contents
from mtg_bot.models import CardFace, CardQuery, CardResult, CardResponse, TextResponse
from mtg_bot.parser import parse_queries
from mtg_bot.renderer.cards import CardRenderer
from mtg_bot.resolver import Resolver
from mtg_bot.scryfall import ScryfallClient, decode_card


def serialized(response):
    return to_line_messages(response)[0].to_dict()


async def test_text_and_image_modes(card):
    card = replace(card, mana_cost="{1}", type_line="Artifact", oracle_text="{T}: Add {C}{C}.", flavor_text="Warm sunlight.")
    results = [CardResult(CardQuery("Sol Ring", "sol ring", mode), card) for mode in ("text", "image")]
    responses = await CardRenderer().render(results)
    messages = to_line_messages(responses)
    plain, picture = [m.to_dict() for m in messages]
    assert plain['contents']['type'] == 'bubble'
    assert card.oracle_text in json.dumps(plain)
    assert card.oracle_text not in json.dumps(picture)
    assert picture['contents']['hero']['url'] == card.image_url
    assert card.scryfall_uri in json.dumps(plain)


@pytest.mark.parametrize('count,kind', [(1,'bubble'),(2,'bubble'),(5,'bubble'),(6,'carousel'),(9,'carousel')])
def test_reply_boundary_and_order(card, count, kind):
    cards = [CardResponse(replace(card, name=f'Card {i}')) for i in range(count)]
    messages = to_line_messages(cards)
    assert len(messages) == (count if count <= 5 else 1)
    assert messages[0].to_dict()['contents']['type'] == kind
    if count > 5:
        titles = [b['body']['contents'][0]['text'] for b in messages[0].to_dict()['contents']['contents']]
        assert titles == [f'Card {i}' for i in range(count)]


def test_error_consumes_slot_and_keeps_order(card):
    responses = [CardResponse(card)] * 3 + [TextResponse('wrong：查不到卡。')] + [CardResponse(card)] * 2
    data = serialized(responses)
    assert data['contents']['type'] == 'carousel'
    bubbles = data['contents']['contents']
    assert len(bubbles) == 6
    assert 'wrong' in json.dumps(bubbles[3])


@pytest.mark.parametrize('layout', ['transform','modal_dfc'])
@pytest.mark.parametrize('mode', ['text','image'])
def test_both_faces_in_one_bubble(card, layout, mode):
    faces = (CardFace('Front', 'https://cards.scryfall.io/front.jpg', oracle_text='Front rules'),
             CardFace('Back', 'https://cards.scryfall.io/back.jpg', oracle_text='Back rules'))
    card = replace(card, layout=layout, image_url=None, faces=faces)
    data = json.dumps(serialized([CardResponse(card, mode)]))
    assert 'front.jpg' in data and 'back.jpg' in data
    if mode == 'text':
        assert 'Front rules' in data and 'Back rules' in data


def test_split_shared_image_both_rules(card):
    card = replace(card, layout='split', faces=(CardFace('A',None,oracle_text='First half'), CardFace('B',None,oracle_text='Second half')))
    data = json.dumps(serialized([CardResponse(card)]))
    assert data.count(card.image_url) == 2  # image URL + tap action; one image
    assert 'First half' in data and 'Second half' in data


async def test_link_cleanup_and_failure_isolation(card):
    card = replace(card, scryfall_uri=card.scryfall_uri+'?utm_source=api&utm_medium=bot')
    responses = await CardRenderer().render([CardResult(CardQuery('bad','bad'),error='not_found'), CardResult(CardQuery('Sol Ring','sol ring'),card)])
    assert isinstance(responses[0], TextResponse)
    assert isinstance(responses[1], CardResponse)
    assert '?' not in responses[1].card.scryfall_uri
    assert 'utm_' not in json.dumps([m.to_dict() for m in to_line_messages(responses)])


async def test_same_card_modes_share_one_api_call(card):
    calls = []
    def mock(request):
        calls.append(request)
        return httpx.Response(200,json={'id':'a','name':'Sol Ring','layout':'normal','scryfall_uri':card.scryfall_uri,'image_uris':{'normal':card.image_url}})
    parsed = parse_queries('[[Sol Ring]] [[!sol ring]] [[ SOL RING ]]')
    assert [q.mode for q in parsed.queries] == ['text','image']
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        resolver = Resolver(ScryfallClient(http,user_agent='test'))
        results = await resolver.resolve_many(parsed.queries)
        assert len(results) == 2 and len(calls) == 1
        await resolver.close()


def test_decode_rules_fields():
    data = {'id':'a','name':'A // B','layout':'transform','scryfall_uri':'https://scryfall.com/card/a',
            'card_faces':[{'name':'A','mana_cost':'{U}','type_line':'Creature','oracle_text':'A rules','power':'1','toughness':'1'},
                          {'name':'B','type_line':'Creature','oracle_text':'B rules','power':'3','toughness':'2'}]}
    card = decode_card(data)
    assert card.faces[0].mana_cost == '{U}'
    assert card.faces[1].power == '3'


def test_missing_image_still_has_rules_and_link(card):
    data = json.dumps(serialized([CardResponse(replace(card,image_url=None,oracle_text='Still readable'))]))
    assert 'Still readable' in data and card.scryfall_uri in data


def test_long_text_stays_within_flex_limits(card):
    card = replace(card,oracle_text='測試長效果'*2000,flavor_text='風味'*2000)
    data = flex_contents([CardResponse(card)] * 9)
    assert len(json.dumps(data,ensure_ascii=False).encode()) <= 49_000
    assert len(serialized([CardResponse(card)] * 9)['altText']) <= 400


def test_core_does_not_import_line_sdk():
    from pathlib import Path
    core = Path(__file__).parents[1] / 'mtg_bot'
    for name in ['models.py','parser.py','resolver.py','scryfall.py','service.py','renderer/cards.py']:
        assert 'linebot' not in (core / name).read_text()
