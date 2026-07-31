import asyncio
import sys
from pathlib import Path

import pytest
from PIL import Image

from app.core.models import BoundingBox, SourceLanguage
from app.services.errors import ProviderError, UnsupportedLanguageError
from app.services.ocr import MangaOcrProvider, PaddleOcrProvider
from app.services.types import ImageInput


@pytest.fixture(autouse=True)
def run_to_thread_inline(monkeypatch):
    calls = []

    async def inline(function, *args):
        calls.append(function)
        return function(*args)

    monkeypatch.setattr(asyncio, "to_thread", inline)
    return calls


def _image(path: Path) -> ImageInput:
    Image.new("RGB", (10, 8), "white").save(path)
    return ImageInput(path=path, width=999, height=999)


def test_manga_ocr_is_lazy_cpu_only_crops_in_thread_and_uses_unknown_confidence(
    tmp_path: Path, run_to_thread_inline
) -> None:
    calls = []

    class Model:
        def __call__(self, crop):
            calls.append(crop.size)
            return "縦書き"

    def factory(**kwargs):
        calls.append(kwargs)
        return Model()

    source = tmp_path / "page.png"
    image = _image(source)
    before = source.read_bytes()
    provider = MangaOcrProvider(model_factory=factory)

    assert calls == []
    first = asyncio.run(
        provider.recognize(
            image,
            BoundingBox(x=2.2, y=1.1, width=4.2, height=3.2),
            SourceLanguage.JA,
        )
    )
    asyncio.run(
        provider.recognize(
            image,
            BoundingBox(x=8, y=6, width=5, height=5),
            SourceLanguage.JA,
        )
    )

    assert calls[0] == {"force_cpu": True}
    assert calls[1] == (5, 4)
    assert calls[2] == (2, 2)
    assert sum(isinstance(call, dict) for call in calls) == 1
    assert len(run_to_thread_inline) == 2
    assert first.source_text == "縦書き"
    assert first.confidence == 0.0
    assert first.provider == provider.name
    assert source.read_bytes() == before


def test_manga_ocr_wraps_dependency_image_and_inference_errors(tmp_path: Path) -> None:
    provider = MangaOcrProvider(model_factory=lambda **kwargs: lambda crop: None)
    with pytest.raises(UnsupportedLanguageError):
        asyncio.run(
            provider.recognize(
                ImageInput(path=tmp_path / "missing.png", width=1, height=1),
                BoundingBox(x=0, y=0, width=1, height=1),
                SourceLanguage.EN,
            )
        )
    with pytest.raises(ProviderError, match="image not found") as image_error:
        asyncio.run(
            provider.recognize(
                ImageInput(path=tmp_path / "missing.png", width=1, height=1),
                BoundingBox(x=0, y=0, width=1, height=1),
                SourceLanguage.JA,
            )
        )
    assert image_error.value.recoverable is True

    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not an image")
    with pytest.raises(ProviderError, match="cannot crop image"):
        asyncio.run(
            provider.recognize(
                ImageInput(path=corrupt, width=1, height=1),
                BoundingBox(x=0, y=0, width=1, height=1),
                SourceLanguage.JA,
            )
        )

    image = _image(tmp_path / "bad-model.png")
    with pytest.raises(ProviderError, match="non-text"):
        asyncio.run(
            provider.recognize(
                image,
                BoundingBox(x=0, y=0, width=1, height=1),
                SourceLanguage.JA,
            )
        )


def test_optional_ocr_modules_are_not_needed_with_injected_factories(tmp_path: Path) -> None:
    assert "manga_ocr" not in sys.modules
    assert "paddleocr" not in sys.modules

    MangaOcrProvider(model_factory=lambda **kwargs: object())
    PaddleOcrProvider(model_factory=lambda **kwargs: object())

    assert "manga_ocr" not in sys.modules
    assert "paddleocr" not in sys.modules


def test_paddle_ocr_maps_languages_uses_cpu_and_caches_per_language(
    tmp_path: Path,
) -> None:
    factory_calls = []
    crop_sizes = []

    class Result:
        rec_texts = ["first", "second"]
        rec_scores = [-0.5, 1.5]

    class Model:
        def predict(self, crop):
            crop_sizes.append(crop.size)
            return [Result()]

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return Model()

    provider = PaddleOcrProvider(model_factory=factory)
    image = _image(tmp_path / "paddle.png")
    languages = (
        SourceLanguage.EN,
        SourceLanguage.KO,
        SourceLanguage.ZH_HANS,
        SourceLanguage.ZH_HANT,
        SourceLanguage.EN,
    )

    results = [
        asyncio.run(
            provider.recognize(
                image,
                BoundingBox(x=1, y=2, width=3, height=4),
                language,
            )
        )
        for language in languages
    ]

    assert [call["lang"] for call in factory_calls] == [
        "en",
        "korean",
        "ch",
        "chinese_cht",
    ]
    assert all(call["device"] == "cpu" for call in factory_calls)
    assert all(
        call["use_doc_orientation_classify"] is False
        and call["use_doc_unwarping"] is False
        and call["use_textline_orientation"] is False
        for call in factory_calls
    )
    assert crop_sizes == [(3, 4)] * len(languages)
    assert all(result.source_text == "first\nsecond" for result in results)
    assert all(result.confidence == 0.5 for result in results)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (
            [{"res": {"rec_texts": ["one", "two"], "rec_scores": [0.2, 0.8]}}],
            ("one\ntwo", 0.5),
        ),
        (
            [type("JsonResult", (), {"json": '{"rec_texts":["json"],"rec_scores":[0.7]}'})()],
            ("json", 0.7),
        ),
    ],
)
def test_paddle_ocr_parses_mapping_and_json_results(tmp_path: Path, raw, expected) -> None:
    class Model:
        def predict(self, crop):
            return raw

    result = asyncio.run(
        PaddleOcrProvider(model_factory=lambda **kwargs: Model()).recognize(
            _image(tmp_path / "shape.png"),
            BoundingBox(x=0, y=0, width=2, height=2),
            SourceLanguage.EN,
        )
    )

    assert (result.source_text, result.confidence) == expected


@pytest.mark.parametrize(
    "raw",
    [
        [],
        [{"rec_texts": [], "rec_scores": []}],
        [{"rec_texts": ["text"], "rec_scores": []}],
        [{"rec_texts": [""], "rec_scores": [0.5]}],
        [{"rec_texts": ["text"], "rec_scores": ["bad"]}],
        [{"unexpected": True}],
    ],
)
def test_paddle_ocr_rejects_malformed_results_and_recovers_on_next_call(
    tmp_path: Path, raw
) -> None:
    responses = [raw, [{"rec_texts": ["recovered"], "rec_scores": [0.9]}]]

    class Model:
        def predict(self, crop):
            return responses.pop(0)

    provider = PaddleOcrProvider(model_factory=lambda **kwargs: Model())
    image = _image(tmp_path / "malformed.png")
    arguments = (
        image,
        BoundingBox(x=0, y=0, width=2, height=2),
        SourceLanguage.EN,
    )

    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.recognize(*arguments))
    assert error.value.recoverable is True
    assert asyncio.run(provider.recognize(*arguments)).source_text == "recovered"


def test_paddle_ocr_rejects_auto_and_bad_image(tmp_path: Path) -> None:
    provider = PaddleOcrProvider(model_factory=lambda **kwargs: object())
    image = ImageInput(path=tmp_path / "missing.png", width=1, height=1)
    bbox = BoundingBox(x=0, y=0, width=1, height=1)

    with pytest.raises(UnsupportedLanguageError):
        asyncio.run(provider.recognize(image, bbox, SourceLanguage.AUTO))
    with pytest.raises(ProviderError, match="image not found"):
        asyncio.run(provider.recognize(image, bbox, SourceLanguage.EN))
