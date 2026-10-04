import io
import httpx
import pytest
from PIL import Image
from mtg_bot.models import Card, CardFace, CardQuery, CardResult, ImageResponse, TextResponse
from mtg_bot.renderer.image import ImageRenderer, card_panels
from mtg_bot.renderer.grid import grid_shape


@pytest.mark.parametrize('count,shape', [(1,(1,1)),(2,(2,1)),(3,(3,1)),(4,(2,2)),(6,(3,2)),(9,(3,3)),(18,(3,6))])
def test_grid_shapes(count,shape):
    assert grid_shape(count) == shape


@pytest.mark.parametrize('layout', ['split','adventure','flip'])
def test_whole_image_precedes_faces(layout):
    card = Card('a','a',layout,'uri','https://cards.scryfall.io/a', (CardFace('face', 'https://cards.scryfall.io/f'),))
    assert card_panels(card) == [('a','https://cards.scryfall.io/a')]


@pytest.mark.parametrize('layout', ['transform','modal_dfc','double_faced_token','reversible_card'])
def test_both_faces(layout):
    card = Card('a','a',layout,'uri',None,(CardFace('front','https://cards.scryfall.io/f'),CardFace('back','https://cards.scryfall.io/b')))
    assert [name for name,_ in card_panels(card)] == ['front','back']


class Publisher:
    async def publish(self, original, preview):
        self.original, self.preview = original, preview
        return ImageResponse('https://bot.test/a.jpg','https://bot.test/p.jpg')


async def test_single_original_preserved_and_image_cache(card,jpeg):
    calls = []
    def mock(request):
        calls.append(request)
        return httpx.Response(200,content=jpeg)
    pub = Publisher()
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        renderer = ImageRenderer(http,pub)
        result = CardResult(CardQuery('Sol Ring','sol ring'),card)
        assert len(await renderer.render([result])) == 2
        assert pub.original == jpeg
        await renderer.render([result])
        assert len(calls) == 1
        assert len(pub.preview) < 1_000_000


async def test_grid_partial_download_failure(card,jpeg):
    pub = Publisher()
    other = Card('b','bad image','normal','uri','https://cards.scryfall.io/bad')
    def mock(request):
        return httpx.Response(404) if request.url.path == '/bad' else httpx.Response(200,content=jpeg)
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        responses = await ImageRenderer(http,pub).render([CardResult(CardQuery('a','a'),card),CardResult(CardQuery('b','b'),other)])
    assert isinstance(responses[0],ImageResponse)
    assert isinstance(responses[1],TextResponse)
    assert 'bad image' in responses[1].text
    with Image.open(io.BytesIO(pub.original)) as image:
        assert image.size == (976,680)


async def test_all_images_fail_returns_text(card):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,content=b'not an image'))) as http:
        responses = await ImageRenderer(http,Publisher()).render([CardResult(CardQuery('a','a'),card)])
    assert len(responses) == 1 and isinstance(responses[0],TextResponse)


async def test_query_failure_does_not_lose_success(card,jpeg):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,content=jpeg))) as http:
        responses = await ImageRenderer(http,Publisher()).render([CardResult(CardQuery('a','a'),card),CardResult(CardQuery('b','b'),error='not_found')])
    assert len(responses) == 2
    assert '查不到卡' in responses[1].text


async def test_image_timeout_retry_and_link(card, jpeg):
    calls = []
    def mock(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.ReadTimeout("test timeout", request=request)
        return httpx.Response(200, content=jpeg)
    async with httpx.AsyncClient(transport=httpx.MockTransport(mock)) as http:
        responses = await ImageRenderer(http, Publisher()).render([CardResult(CardQuery("a", "a"), card)])
    assert len(calls) == 2
    assert calls[0].headers["User-Agent"] == "mtg-line-bot/0.1"
    assert isinstance(responses[0], ImageResponse)
    assert card.scryfall_uri in responses[1].text


async def test_image_http_failure_logged_and_link_preserved(card, caplog):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(403))) as http:
        responses = await ImageRenderer(http, Publisher()).render([CardResult(CardQuery("a", "a"), card)])
    assert "status=403" in caplog.text
    assert card.scryfall_uri in responses[0].text
    assert "卡圖暫時無法取得" in responses[0].text
