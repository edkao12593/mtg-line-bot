from mtg_bot.cache import TTLCache


def test_expiry_and_lru():
    now = [0]
    cache = TTLCache(2, clock=lambda: now[0])
    cache.put("a", 1, 10)
    cache.put("b", 2, 10)
    assert cache.get("a") == 1
    cache.put("c", 3, 10)
    assert cache.get("b") is None
    now[0] = 10
    assert cache.get("a") is None
    assert cache.get("c") is None
