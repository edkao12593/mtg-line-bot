import io
import pytest
from PIL import Image
from mtg_bot.models import Card


@pytest.fixture
def card():
    return Card('id', 'Sol Ring', 'normal', 'https://scryfall.com/card/test', 'https://cards.scryfall.io/normal/front/a/b/test.jpg')


@pytest.fixture
def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (488, 680), '#925e28').save(out, 'JPEG')
    return out.getvalue()


@pytest.fixture(autouse=True)
def offline_test_environment(monkeypatch):
    # Mock transport tests must not require the host's optional SOCKS proxy.
    for name in ('ALL_PROXY','all_proxy','HTTPS_PROXY','https_proxy','HTTP_PROXY','http_proxy'):
        monkeypatch.delenv(name,raising=False)
