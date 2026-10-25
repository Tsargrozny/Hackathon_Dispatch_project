from __future__ import annotations

import httpx

USER_AGENT = "MedRoute/1.0 (hackathon demo; organ transport dispatcher)"

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1),
            timeout=httpx.Timeout(12, connect=6),
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
    return _client


async def close() -> None:
    if _client is not None and not _client.is_closed:
        await _client.aclose()
