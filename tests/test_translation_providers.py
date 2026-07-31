import asyncio
import json
from email.message import Message
from urllib import error
from uuid import UUID, uuid4

import pytest

from app.core.models import (
    Character,
    GlossaryEntry,
    ProviderConfiguration,
    SourceLanguage,
    TranslationContext,
    TranslationInput,
)
from app.services.errors import ProviderError
from app.services.translation import (
    OllamaTranslationProvider,
    OpenAICompatibleTranslationProvider,
)
from app.services.translation.http import MAX_RESPONSE_BYTES, _post_json_sync

FIRST_ID = UUID("00000000-0000-0000-0000-000000000001")
SECOND_ID = UUID("00000000-0000-0000-0000-000000000002")


def _configuration(provider: str, *, retry_count: int = 0) -> ProviderConfiguration:
    return ProviderConfiguration(
        provider=provider,
        base_url="http://translator.local/",
        model="configured-model",
        temperature=0.4,
        timeout_seconds=3,
        retry_count=retry_count,
        instructions="ใช้สำนวนสุภาพ",
    )


def _blocks() -> list[TranslationInput]:
    return [
        TranslationInput(
            id=FIRST_ID,
            source_language=SourceLanguage.KO,
            source_text="다녀왔어?",
            reading_order=8,
        ),
        TranslationInput(
            id=SECOND_ID,
            source_language=SourceLanguage.EN,
            source_text="I'm home.",
            reading_order=2,
        ),
    ]


def _context() -> TranslationContext:
    return TranslationContext(
        default_source_language=SourceLanguage.JA,
        target_language="th",
        characters=[Character(name="ยูริ", personality="สุภาพ")],
        glossary=[
            GlossaryEntry(
                source_term="home",
                target_term="บ้าน",
                source_language=SourceLanguage.EN,
            )
        ],
        previous_summary="พบกันที่บ้าน",
        translation_note="คงน้ำเสียงเดิม",
    )


def _content(items: list[dict] | None = None) -> str:
    return json.dumps(
        {
            "translations": items
            or [
                {"id": str(SECOND_ID), "translated_text": "กลับมาแล้ว", "note": ""},
                {"id": str(FIRST_ID), "translated_text": "กลับมาแล้วเหรอ", "note": "กันเอง"},
            ]
        },
        ensure_ascii=False,
    )


def test_openai_request_and_response_are_text_only_and_ordered() -> None:
    seen = {}

    async def transport(**request):
        seen.update(request)
        return {"choices": [{"message": {"content": _content()}}]}

    provider = OpenAICompatibleTranslationProvider(
        _configuration("openai-compatible"),
        api_key="top-secret",
        transport=transport,
        retry_delay=0,
    )
    results = asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert seen["url"] == "http://translator.local/chat/completions"
    assert seen["timeout"] == 3
    assert seen["headers"]["Authorization"] == "Bearer top-secret"
    assert seen["payload"]["model"] == "configured-model"
    assert seen["payload"]["temperature"] == 0.4
    assert seen["payload"]["response_format"] == {"type": "json_object"}
    assert [message["role"] for message in seen["payload"]["messages"]] == ["system", "user"]
    serialized = seen["payload"]["messages"][1]["content"]
    request_data = json.loads(serialized)
    assert request_data["blocks"] == [
        {
            "id": str(FIRST_ID),
            "reading_order": 8,
            "source_language": "ko",
            "source_text": "다녀왔어?",
        },
        {
            "id": str(SECOND_ID),
            "reading_order": 2,
            "source_language": "en",
            "source_text": "I'm home.",
        },
    ]
    assert request_data["context"]["glossary"][0]["source_term"] == "home"
    assert "top-secret" not in repr(provider)
    assert "path" not in serialized.lower()
    assert [result.id for result in results] == [FIRST_ID, SECOND_ID]
    assert [result.translated_text for result in results] == [
        "กลับมาแล้วเหรอ",
        "กลับมาแล้ว",
    ]


def test_ollama_request_and_response_use_native_envelope_without_key() -> None:
    seen = {}

    async def transport(**request):
        seen.update(request)
        return {"message": {"content": _content()}}

    provider = OllamaTranslationProvider(
        _configuration("ollama"),
        transport=transport,
        retry_delay=0,
    )
    asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert seen["url"] == "http://translator.local/api/chat"
    assert seen["payload"]["stream"] is False
    assert seen["payload"]["format"] == "json"
    assert seen["payload"]["options"] == {"temperature": 0.4}
    assert "Authorization" not in seen["headers"]


@pytest.mark.parametrize(
    "content,error_text",
    [
        ("not json", "invalid translation JSON"),
        (
            json.dumps(
                {
                    "translations": [
                        {
                            "id": str(FIRST_ID),
                            "translated_text": "ok",
                            "note": "",
                            "extra": True,
                        },
                        {"id": str(SECOND_ID), "translated_text": "ok"},
                    ]
                }
            ),
            "extra",
        ),
        (
            json.dumps(
                {
                    "translations": [
                        {"id": str(FIRST_ID), "translated_text": 3},
                        {"id": str(SECOND_ID), "translated_text": "ok"},
                    ]
                }
            ),
            "string",
        ),
    ],
)
def test_invalid_assistant_json_or_schema_is_rejected(content: str, error_text: str) -> None:
    async def transport(**request):
        return {"message": {"content": content}}

    provider = OllamaTranslationProvider(
        _configuration("ollama"),
        transport=transport,
        retry_delay=0,
    )
    with pytest.raises(ProviderError, match=error_text):
        asyncio.run(provider.translate_blocks(_blocks(), _context()))


def test_provider_envelope_accepts_harmless_unknown_metadata() -> None:
    async def transport(**request):
        return {
            "message": {"content": _content(), "provider_message_metadata": True},
            "provider_metadata": {"request_id": "local-1"},
        }

    provider = OllamaTranslationProvider(
        _configuration("ollama"),
        transport=transport,
        retry_delay=0,
    )
    results = asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert [result.id for result in results] == [FIRST_ID, SECOND_ID]


@pytest.mark.parametrize("response_kind", ["missing", "duplicate", "unknown"])
def test_invalid_id_mapping_is_rejected(response_kind: str) -> None:
    items = [
        {"id": str(FIRST_ID), "translated_text": "หนึ่ง"},
        {"id": str(SECOND_ID), "translated_text": "สอง"},
    ]
    if response_kind == "missing":
        items.pop()
    elif response_kind == "duplicate":
        items[1]["id"] = str(FIRST_ID)
    else:
        items[1]["id"] = str(uuid4())

    async def transport(**request):
        return {"message": {"content": _content(items)}}

    provider = OllamaTranslationProvider(
        _configuration("ollama"),
        transport=transport,
        retry_delay=0,
    )
    with pytest.raises(ProviderError, match=response_kind):
        asyncio.run(provider.translate_blocks(_blocks(), _context()))


def test_empty_source_hallucination_is_rejected() -> None:
    blocks = [
        TranslationInput(
            id=FIRST_ID,
            source_language=SourceLanguage.ZH_HANS,
            source_text="",
        )
    ]

    async def transport(**request):
        return {
            "choices": [
                {
                    "message": {
                        "content": _content(
                            [{"id": str(FIRST_ID), "translated_text": "ข้อความที่แต่งขึ้น"}]
                        )
                    }
                }
            ]
        }

    provider = OpenAICompatibleTranslationProvider(
        _configuration("openai-compatible"),
        transport=transport,
        retry_delay=0,
    )
    with pytest.raises(ProviderError, match="empty source"):
        asyncio.run(provider.translate_blocks(blocks, _context()))


def test_recoverable_errors_retry_only_to_configured_limit() -> None:
    calls = 0

    async def transport(**request):
        nonlocal calls
        calls += 1
        raise ProviderError("HTTP 503", recoverable=True)

    provider = OllamaTranslationProvider(
        _configuration("ollama", retry_count=2),
        transport=transport,
        retry_delay=0,
    )
    with pytest.raises(ProviderError, match="503") as raised:
        asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert raised.value.recoverable is True
    assert calls == 3


def test_validation_failure_is_retried_and_can_recover() -> None:
    calls = 0

    async def transport(**request):
        nonlocal calls
        calls += 1
        content = "invalid" if calls == 1 else _content()
        return {"message": {"content": content}}

    provider = OllamaTranslationProvider(
        _configuration("ollama", retry_count=1),
        transport=transport,
        retry_delay=0,
    )
    results = asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert calls == 2
    assert len(results) == 2


def test_non_recoverable_error_is_not_retried_or_leaked() -> None:
    calls = 0

    async def transport(**request):
        nonlocal calls
        calls += 1
        raise ProviderError("provider returned HTTP 401", recoverable=False)

    provider = OpenAICompatibleTranslationProvider(
        _configuration("openai-compatible", retry_count=3),
        api_key="never-print-this",
        transport=transport,
        retry_delay=0,
    )
    with pytest.raises(ProviderError) as raised:
        asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert calls == 1
    assert raised.value.recoverable is False
    assert "never-print-this" not in str(raised.value)


def test_injected_timeout_is_retried_with_redacted_error() -> None:
    calls = 0

    async def transport(**request):
        nonlocal calls
        calls += 1
        raise TimeoutError("top-secret")

    provider = OpenAICompatibleTranslationProvider(
        _configuration("openai-compatible", retry_count=1),
        api_key="top-secret",
        transport=transport,
        retry_delay=0,
    )
    with pytest.raises(ProviderError) as raised:
        asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert calls == 2
    assert raised.value.recoverable is True
    assert "top-secret" not in str(raised.value)


class _Headers(Message):
    pass


class _Response:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self._body = body
        self.headers = _Headers()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit: int) -> bytes:
        return self._body[:limit]


@pytest.mark.parametrize(
    ("body", "content_type", "message"),
    [
        (b"[]", "application/json", "must be an object"),
        (b"invalid", "application/json", "invalid JSON"),
        (b"{}", "text/plain", "non-JSON"),
        (b"x" * (MAX_RESPONSE_BYTES + 1), "application/json", "exceeded"),
    ],
)
def test_http_transport_rejects_invalid_or_oversized_responses(
    monkeypatch,
    body: bytes,
    content_type: str,
    message: str,
) -> None:
    monkeypatch.setattr(
        "app.services.translation.http.request.urlopen",
        lambda outgoing, timeout: _Response(body, content_type),
    )

    with pytest.raises(ProviderError, match=message):
        _post_json_sync("http://local", {}, {"Authorization": "Bearer secret"}, 2)


@pytest.mark.parametrize(
    ("failure", "recoverable", "message"),
    [
        (
            error.HTTPError("http://secret", 400, "bad", {}, None),
            False,
            "HTTP 400",
        ),
        (
            error.HTTPError("http://secret", 429, "busy", {}, None),
            True,
            "HTTP 429",
        ),
        (
            error.HTTPError("http://secret", 503, "down", {}, None),
            True,
            "HTTP 503",
        ),
        (error.URLError("secret"), True, "network request failed"),
        (TimeoutError("secret"), True, "timed out"),
    ],
)
def test_http_transport_classifies_and_redacts_failures(
    monkeypatch,
    failure: Exception,
    recoverable: bool,
    message: str,
) -> None:
    def fail(outgoing, timeout):
        raise failure

    monkeypatch.setattr("app.services.translation.http.request.urlopen", fail)
    with pytest.raises(ProviderError, match=message) as raised:
        _post_json_sync(
            "http://user:secret@local",
            {},
            {"Authorization": "Bearer secret"},
            2,
        )

    assert raised.value.recoverable is recoverable
    assert "secret" not in str(raised.value)
