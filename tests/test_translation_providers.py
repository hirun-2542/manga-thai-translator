import asyncio
import json
from email.message import Message
from pathlib import Path
from urllib import error
from uuid import UUID, uuid4

import pytest
from PIL import Image

from app.core.models import (
    BlockStatus,
    Character,
    GlossaryEntry,
    Page,
    ProviderConfiguration,
    SourceLanguage,
    TranslationContext,
    TranslationInput,
)
from app.services.errors import ProviderError
from app.services.translation import (
    CodexCliTranslationProvider,
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


def test_codex_cli_is_text_only_ephemeral_read_only_and_uses_schema(monkeypatch) -> None:
    seen = {}

    class Process:
        returncode = 0

        async def communicate(self, prompt):
            seen["prompt"] = prompt
            return _content().encode(), b"ignored stderr"

    async def create_subprocess_exec(*command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        schema_path = command[command.index("--output-schema") + 1]
        seen["schema_path"] = schema_path
        seen["schema"] = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        return Process()

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )
    configuration = _configuration("codex-cli")
    configuration.model = "default"
    provider = CodexCliTranslationProvider(configuration, retry_delay=0)

    results = asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert seen["command"][:2] == ("codex", "exec")
    assert seen["command"][-1] == "-"
    assert "--ephemeral" in seen["command"]
    assert seen["command"][seen["command"].index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in seen["command"]
    assert "--ignore-user-config" in seen["command"]
    assert "--ignore-rules" in seen["command"]
    assert seen["command"][seen["command"].index("--color") + 1] == "never"
    assert "--model" not in seen["command"]
    assert "--image" not in seen["command"]
    assert seen["kwargs"] == {
        "stdin": asyncio.subprocess.PIPE,
        "stdout": asyncio.subprocess.PIPE,
        "stderr": asyncio.subprocess.PIPE,
        "cwd": str(Path(seen["schema_path"]).parent),
    }
    messages = json.loads(seen["prompt"])
    assert [message["role"] for message in messages] == ["system", "user"]
    assert "do not inspect files or run tools" in messages[0]["content"].lower()
    assert "images" not in seen["prompt"].decode().lower()
    assert seen["schema"]["additionalProperties"] is False
    assert not Path(seen["schema_path"]).exists()
    assert not Path(seen["kwargs"]["cwd"]).exists()
    assert [result.id for result in results] == [FIRST_ID, SECOND_ID]


def test_codex_cli_image_translates_clamps_and_normalizes_locally(monkeypatch, tmp_path) -> None:
    image_path = tmp_path / "หน้า.png"
    image_path.write_bytes(b"not opened by test")
    original_bytes = image_path.read_bytes()
    page = Page(source_path=str(image_path), width=100, height=100)
    seen = {}
    response = json.dumps(
        {
            "blocks": [
                {
                    "bbox": {"x": -5, "y": 10, "width": 30, "height": 25},
                    "reading_order": 8,
                    "source_language": "ja",
                    "source_text": "ただいま",
                    "translated_text": "กลับมาแล้ว",
                    "note": "",
                },
                {
                    "bbox": {"x": 90, "y": 90, "width": 30, "height": 30},
                    "reading_order": 2,
                    "source_language": "en",
                    "source_text": "Welcome home",
                    "translated_text": "ยินดีต้อนรับกลับบ้าน",
                    "note": "",
                },
            ]
        },
        ensure_ascii=False,
    )

    class Process:
        returncode = 0

        async def communicate(self, prompt):
            seen["prompt"] = prompt.decode()
            return response.encode(), b"ignored secret stderr"

    async def create_subprocess_exec(*command, **kwargs):
        seen["command"] = command
        seen["cwd"] = kwargs["cwd"]
        schema_path = Path(command[command.index("--output-schema") + 1])
        seen["schema_path"] = schema_path
        seen["schema"] = json.loads(schema_path.read_text(encoding="utf-8"))
        return Process()

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )
    configuration = _configuration("codex-cli").model_copy(update={"uploads_images": True})
    provider = CodexCliTranslationProvider(configuration, retry_delay=0)

    blocks = asyncio.run(provider.translate_page_image(page, image_path, _context()))

    image_index = seen["command"].index("--image")
    assert seen["command"][image_index + 1] == str(image_path.resolve())
    assert seen["command"].count("--image") == 1
    assert seen["command"][-1] == "-"
    assert seen["schema"]["properties"]["blocks"]["items"]["properties"]["source_language"][
        "enum"
    ] == ["ja", "en", "ko", "zh-Hans", "zh-Hant"]
    assert "Read every dialogue and narration block" in seen["prompt"]
    assert "exactly one block per distinct speech-bubble or narration container" in seen["prompt"]
    assert "Do not omit, invent,\nsplit, or merge blocks" in seen["prompt"]
    assert "preserving every character and exact\npunctuation" in seen["prompt"]
    assert "tightly\nenclose all original source-text glyphs" in seen["prompt"]
    assert "with 4 pixels of padding on every side" in seen["prompt"]
    assert "excluding the bubble border, tail, character art, and adjacent panels" in seen["prompt"]
    assert "largest safe rectangular interior" not in seen["prompt"]
    assert "crop-local pixel coordinates with crop origin (0, 0)" in seen["prompt"]
    assert "natural Thai" in seen["prompt"]
    assert "Do not inspect the filesystem or run tools" in seen["prompt"]
    assert "half-open local core y range [0,100)" in seen["prompt"]
    assert "global y" not in seen["prompt"]
    assert not seen["schema_path"].exists()
    assert not Path(seen["cwd"]).exists()
    assert [block.reading_order for block in blocks] == [1, 2]
    assert [block.source_text for block in blocks] == ["Welcome home", "ただいま"]
    assert blocks[0].bbox.model_dump() == {"x": 90.0, "y": 90.0, "width": 10.0, "height": 10.0}
    assert blocks[1].bbox.model_dump() == {"x": 0.0, "y": 10.0, "width": 25.0, "height": 25.0}
    assert len({block.id for block in blocks}) == 2
    assert all(block.page_id == page.id for block in blocks)
    assert all(block.ocr_provider == "codex-image" for block in blocks)
    assert all(block.status is BlockStatus.TRANSLATED for block in blocks)
    assert [block.source_language for block in blocks] == [SourceLanguage.EN, SourceLanguage.JA]
    assert image_path.read_bytes() == original_bytes


def test_codex_cli_tall_image_processes_lossless_overlapping_tiles_sequentially(
    monkeypatch, tmp_path
) -> None:
    image_path = tmp_path / "tall.png"
    Image.new("RGB", (80, 5000), "white").save(image_path, format="PNG")
    original_bytes = image_path.read_bytes()
    page = Page(source_path=str(image_path), width=80, height=5000)
    seen = {"commands": [], "prompts": [], "tile_paths": []}
    responses = [
        {
            "blocks": [
                {
                    "bbox": {"x": -5, "y": 10, "width": 30, "height": 20},
                    "reading_order": 9,
                    "source_language": "ja",
                    "source_text": "first",
                    "translated_text": "หนึ่ง",
                    "note": "",
                },
                {
                    "bbox": {"x": 5, "y": 1655, "width": 20, "height": 30},
                    "reading_order": 1,
                    "source_language": "en",
                    "source_text": "boundary duplicate",
                    "translated_text": "รอยต่อ",
                    "note": "",
                },
            ]
        },
        {
            "blocks": [
                {
                    "bbox": {"x": 5, "y": 200, "width": 20, "height": 30},
                    "reading_order": 9,
                    "source_language": "ko",
                    "source_text": "second",
                    "translated_text": "สอง",
                    "note": "",
                },
                {
                    "bbox": {"x": 5, "y": 89, "width": 20, "height": 30},
                    "reading_order": 1,
                    "source_language": "en",
                    "source_text": "boundary duplicate",
                    "translated_text": "รอยต่อ",
                    "note": "",
                },
            ]
        },
        {
            "blocks": [
                {
                    "bbox": {"x": 10, "y": 1750, "width": 30, "height": 100},
                    "reading_order": 0,
                    "source_language": "zh-Hans",
                    "source_text": "third",
                    "translated_text": "สาม",
                    "note": "",
                }
            ]
        },
    ]

    class Process:
        def __init__(self, response):
            self.response = response

        returncode = 0

        async def communicate(self, prompt):
            seen["prompts"].append(prompt.decode())
            return json.dumps(self.response, ensure_ascii=False).encode(), b""

    async def create_subprocess_exec(*command, **kwargs):
        tile_paths = [
            Path(command[index + 1])
            for index, argument in enumerate(command)
            if argument == "--image"
        ]
        assert len(tile_paths) == 1
        assert tile_paths[0].exists()
        assert tile_paths[0].suffix == ".png"
        assert tile_paths[0].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert [path.name for path in Path(kwargs["cwd"]).iterdir()] == [
            "translation-response.schema.json"
        ]
        seen["commands"].append(command)
        seen["tile_paths"].extend(tile_paths)
        return Process(responses[len(seen["commands"]) - 1])

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )
    configuration = _configuration("codex-cli").model_copy(update={"uploads_images": True})
    provider = CodexCliTranslationProvider(configuration, retry_delay=0)

    blocks = asyncio.run(provider.translate_page_image(page, image_path, _context()))

    assert len(seen["commands"]) == 3
    assert all(command.count("--image") == 1 for command in seen["commands"])
    assert len(seen["tile_paths"]) == 3
    assert image_path.resolve() not in seen["tile_paths"]
    assert all(not path.exists() for path in seen["tile_paths"])
    assert "half-open local core y range [0,1666)" in seen["prompts"][0]
    assert "half-open local core y range [100,1767)" in seen["prompts"][1]
    assert "half-open local core y range [100,1767)" in seen["prompts"][2]
    assert all("global y" not in prompt for prompt in seen["prompts"])
    assert all("single attached crop" in prompt for prompt in seen["prompts"])
    assert [block.reading_order for block in blocks] == [1, 2, 3, 4]
    assert [block.source_text for block in blocks] == [
        "first",
        "boundary duplicate",
        "second",
        "third",
    ]
    assert blocks[0].bbox.model_dump() == {"x": 0.0, "y": 10.0, "width": 25.0, "height": 20.0}
    assert blocks[1].bbox.y == 1655
    assert blocks[2].bbox.y == 1766
    assert blocks[3].bbox.model_dump() == {
        "x": 10.0,
        "y": 4983.0,
        "width": 30.0,
        "height": 17.0,
    }
    assert image_path.read_bytes() == original_bytes


@pytest.mark.parametrize(("uploads_images", "create_image"), [(False, True), (True, False)])
def test_codex_cli_image_requires_opt_in_and_existing_file(
    monkeypatch, tmp_path, uploads_images: bool, create_image: bool
) -> None:
    image_path = tmp_path / "page.png"
    if create_image:
        image_path.write_bytes(b"image")
    page = Page(source_path=str(image_path), width=100, height=100)

    async def unexpected_subprocess(*command, **kwargs):
        pytest.fail("Codex CLI must not run before image input validation")

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        unexpected_subprocess,
    )
    configuration = _configuration("codex-cli").model_copy(
        update={"uploads_images": uploads_images}
    )
    provider = CodexCliTranslationProvider(configuration, retry_delay=0)

    with pytest.raises(ProviderError) as raised:
        asyncio.run(provider.translate_page_image(page, image_path, _context()))

    assert raised.value.recoverable is False


def test_codex_cli_image_invalid_response_retries(monkeypatch, tmp_path) -> None:
    image_path = tmp_path / "page.png"
    image_path.write_bytes(b"image")
    page = Page(source_path=str(image_path), width=100, height=100)
    calls = 0

    class Process:
        returncode = 0

        async def communicate(self, prompt):
            nonlocal calls
            calls += 1
            source_language = "auto" if calls == 1 else "ko"
            return json.dumps(
                {
                    "blocks": [
                        {
                            "bbox": {"x": 1, "y": 2, "width": 3, "height": 4},
                            "reading_order": 0,
                            "source_language": source_language,
                            "source_text": "안녕",
                            "translated_text": "สวัสดี",
                            "note": "",
                        }
                    ]
                }
            ).encode(), b""

    async def create_subprocess_exec(*command, **kwargs):
        return Process()

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )
    configuration = _configuration("codex-cli", retry_count=1).model_copy(
        update={"uploads_images": True}
    )
    provider = CodexCliTranslationProvider(configuration, retry_delay=0)

    blocks = asyncio.run(provider.translate_page_image(page, image_path, _context()))

    assert calls == 2
    assert blocks[0].source_language is SourceLanguage.KO


def test_codex_cli_passes_non_default_model(monkeypatch) -> None:
    seen = {}

    class Process:
        returncode = 0

        async def communicate(self, prompt):
            return _content().encode(), b""

    async def create_subprocess_exec(*command, **kwargs):
        seen["command"] = command
        return Process()

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )
    provider = CodexCliTranslationProvider(
        _configuration("codex-cli"),
        retry_delay=0,
    )

    asyncio.run(provider.translate_blocks(_blocks(), _context()))

    model_index = seen["command"].index("--model")
    assert seen["command"][model_index + 1] == "configured-model"


def test_codex_cli_missing_executable_is_clear_and_not_retried(monkeypatch) -> None:
    calls = 0

    async def create_subprocess_exec(*command, **kwargs):
        nonlocal calls
        calls += 1
        raise FileNotFoundError

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )
    provider = CodexCliTranslationProvider(
        _configuration("codex-cli", retry_count=2),
        retry_delay=0,
    )

    with pytest.raises(ProviderError, match="executable was not found") as raised:
        asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert calls == 1
    assert raised.value.recoverable is False


def test_codex_cli_nonzero_exit_retries_without_stderr_leak(monkeypatch) -> None:
    calls = 0

    class Process:
        returncode = 7

        async def communicate(self, prompt):
            return b"", b"top-secret"

    async def create_subprocess_exec(*command, **kwargs):
        nonlocal calls
        calls += 1
        return Process()

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )
    provider = CodexCliTranslationProvider(
        _configuration("codex-cli", retry_count=1),
        retry_delay=0,
    )

    with pytest.raises(ProviderError, match="status 7") as raised:
        asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert calls == 2
    assert "top-secret" not in str(raised.value)


def test_codex_cli_timeout_terminates_process_and_retries(monkeypatch) -> None:
    processes = []

    class Process:
        returncode = None
        terminated = False

        async def communicate(self, prompt):
            await asyncio.Event().wait()

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        async def wait(self):
            return self.returncode

    async def create_subprocess_exec(*command, **kwargs):
        process = Process()
        processes.append(process)
        return process

    monkeypatch.setattr(
        "app.services.translation.codex_cli.asyncio.create_subprocess_exec",
        create_subprocess_exec,
    )
    configuration = _configuration("codex-cli", retry_count=1).model_copy(
        update={"timeout_seconds": 0.01}
    )
    provider = CodexCliTranslationProvider(configuration, retry_delay=0)

    with pytest.raises(ProviderError, match="timed out") as raised:
        asyncio.run(provider.translate_blocks(_blocks(), _context()))

    assert len(processes) == 2
    assert all(process.terminated for process in processes)
    assert raised.value.recoverable is True


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
