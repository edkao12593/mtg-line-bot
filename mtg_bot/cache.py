import time
from collections import OrderedDict
from typing import Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Cache recent lookups, removing expired entries when they are read.

    Use within one event loop; the monotonic clock keeps TTLs independent of
    changes to the system time.
    """

    def __init__(self, capacity: int = 1024, clock=time.monotonic):
        self.capacity = capacity
        self.clock = clock
        self.items: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        entry = self.items.get(key)
        if entry is None:
            return None
        expiry, value = entry
        if expiry <= self.clock():
            del self.items[key]
            return None
        self.items.move_to_end(key)
        return value

    def put(self, key: str, value: T, ttl: float):
        self.items[key] = (self.clock() + ttl, value)
        self.items.move_to_end(key)
        while len(self.items) > self.capacity:
            self.items.popitem(last=False)
