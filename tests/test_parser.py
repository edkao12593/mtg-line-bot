import pytest
from mtg_bot.parser import parse_queries


@pytest.mark.parametrize('text,names', [
    ('[[Sol Ring]]', ['Sol Ring']),
    ('我覺得 [[Sol Ring]] 跟 [[Mana Vault]] 很強', ['Sol Ring', 'Mana Vault']),
    ('這 combo 是\n[[Peregrine Drake]]\n[[Ghostly Flicker]]\n[[Archaeomancer]]', ['Peregrine Drake', 'Ghostly Flicker', 'Archaeomancer']),
    ('[[Sol Ring]] [[ sol   ring ]] [[!SOL RING]]', ['Sol Ring', 'SOL RING']),
    ('hello', []), ('[[ ]] [[!]]', []), ('[[Sol Ring]', []),
    ('{{c:u}} !oracle Sol Ring', []),
])
def test_global_parser(text, names):
    assert [q.name for q in parse_queries(text).queries] == names


def test_alias_and_limit():
    result = parse_queries('[[!A]] [[B]] [[C]]', max_queries=2)
    assert result.queries[0].mode == 'image'
    assert len(result.queries) == 2
    assert result.notices


def test_long_and_unsupported():
    result = parse_queries('[[Sol Ring|CMM]] [[' + 'x' * 201 + ']] [[A]]')
    assert [q.name for q in result.queries] == ['A']
    assert len(result.notices) == 2
