"""Small, bounded stdlib JSON HTTP transport."""

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from urllib import error, request

from app.services.errors import ProviderError

MAX_RESPONSE_BYTES = 1_048_576
type JsonObject = dict[str, Any]
type AsyncTransport = Callable[..., Awaitable[JsonObject]]


async def post_json(
    *,
    url: str,
    payload: JsonObject,
    headers: Mapping[str, str],
    timeout: float,
) -> JsonObject:
    return await asyncio.to_thread(_post_json_sync, url, payload, headers, timeout)


def _post_json_sync(
    url: str,
    payload: JsonObject,
    headers: Mapping[str, str],
    timeout: float,
) -> JsonObject:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    outgoing = request.Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with request.urlopen(outgoing, timeout=timeout) as response:
            content_type = response.headers.get_content_type()
            if content_type != "application/json" and not content_type.endswith("+json"):
                raise ProviderError(
                    f"provider returned non-JSON content type {content_type!r}",
                    recoverable=True,
                )
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except error.HTTPError as exc:
        recoverable = exc.code == 429 or exc.code >= 500
        raise ProviderError(
            f"provider returned HTTP {exc.code}",
            recoverable=recoverable,
        ) from None
    except TimeoutError:
        raise ProviderError(
            f"provider request timed out after {timeout:g} seconds",
            recoverable=True,
        ) from None
    except error.URLError:
        raise ProviderError("provider network request failed", recoverable=True) from None
    except OSError:
        raise ProviderError("provider connection failed", recoverable=True) from None

    if len(raw) > MAX_RESPONSE_BYTES:
        raise ProviderError(
            f"provider response exceeded {MAX_RESPONSE_BYTES} bytes",
            recoverable=True,
        )
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProviderError("provider returned invalid JSON", recoverable=True) from None
    if not isinstance(decoded, dict):
        raise ProviderError("provider JSON body must be an object", recoverable=True)
    return decoded
