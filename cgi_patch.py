import re

path = "cgi_client.py"
with open(path, encoding="utf-8") as f:
    s = f.read()

# Add imports if needed
if "_FIND_LOCKS" not in s:
    # Add after _LOCKS_GUARD
    old = "_LOCKS_GUARD = threading.Lock()\n\n\nclass DvrCgi:"
    new = """_LOCKS_GUARD = threading.Lock()

_FIND_LOCKS: dict[tuple[str, int], asyncio.Lock] = {}
_FIND_LOCKS_GUARD = asyncio.Lock()

_CACHE: dict[tuple[object, ...], tuple[float, dict[str, Any]]] = {}
_CACHE_GUARD = threading.Lock()


def _cache_get(key: tuple[object, ...], now: float) -> dict[str, Any] | None:
    with _CACHE_GUARD:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        expires, value = entry
        if expires < now:
            _CACHE.pop(key, None)
            return None
        # Return a shallow copy to avoid mutation
        return dict(value)


def _cache_put(key: tuple[object, ...], value: dict[str, Any], expires: float) -> None:
    with _CACHE_GUARD:
        _CACHE[key] = (expires, dict(value))


async def _device_lock(host: str, port: int) -> asyncio.Lock:
    key = (host, int(port))
    async with _FIND_LOCKS_GUARD:
        lock = _FIND_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _FIND_LOCKS[key] = lock
        return lock


def clear_cache() -> None:
    \"\"\"Drop cached finder results (used by tests and manual diagnostics).\"\"\"
    with _CACHE_GUARD:
        _CACHE.clear()


class DvrCgi:"""
    if old not in s:
        # try alternative
        pass
    else:
        s2 = s.replace(old, new)
        with open(path, "w", encoding="utf-8") as f:
            f.write(s2)
        print("done1")
        exit()

# Fallback: append after locks
if "def clear_cache" not in s:
    pass

print("skip")
