import asyncio
import json
import subprocess
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from app.core.models import BoundingBox, SourceLanguage
from app.services.errors import ProviderError, UnsupportedLanguageError
from app.services.ocr import MangaOcrProvider, PaddleOcrProvider, paddle_worker
from app.services.ocr.paddle_ocr import _as_array
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
    assert all(call["enable_mkldnn"] is False for call in factory_calls)
    assert all(
        call["use_doc_orientation_classify"] is False
        and call["use_doc_unwarping"] is False
        and call["use_textline_orientation"] is False
        for call in factory_calls
    )
    assert crop_sizes == [(3, 4)] * len(languages)
    assert all(result.source_text == "first\nsecond" for result in results)
    assert all(result.confidence == 0.5 for result in results)


def test_paddle_detection_parses_groups_and_reuses_english_model(tmp_path: Path) -> None:
    source = tmp_path / "detection.png"
    image = _image(source)
    before = source.read_bytes()
    factory_calls = []
    responses = [
        [
            type(
                "JsonResult",
                (),
                {
                    "json": {
                        "res": {
                            "rec_boxes": [
                                [10, 10, 80, 30],
                                [180, 12, 230, 32],
                                [12, 36, 78, 56],
                                [10, 106, 80, 306],
                            ],
                            "rec_scores": [1.4, 0.8, -0.2, 0.6],
                        }
                    }
                },
            )()
        ],
        [{"rec_texts": ["recognized"], "rec_scores": [0.9]}],
    ]

    class Model:
        def predict(self, crop):
            return responses.pop(0)

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return Model()

    provider = PaddleOcrProvider(model_factory=factory)

    regions = asyncio.run(provider.detect(image))
    result = asyncio.run(
        provider.recognize(
            image,
            BoundingBox(x=0, y=0, width=2, height=2),
            SourceLanguage.EN,
        )
    )

    assert len(factory_calls) == 1
    assert [region.bbox.model_dump() for region in regions] == [
        {"x": 10.0, "y": 10.0, "width": 70.0, "height": 46.0},
        {"x": 180.0, "y": 12.0, "width": 50.0, "height": 20.0},
        {"x": 10.0, "y": 106.0, "width": 70.0, "height": 200.0},
    ]
    assert [region.confidence for region in regions] == [0.5, 0.8, 0.6]
    assert result.source_text == "recognized"
    assert source.read_bytes() == before


def test_paddle_detection_returns_empty_and_rejects_malformed(tmp_path: Path) -> None:
    responses = [
        [],
        [{"res": {"rec_boxes": [[1, 2, 3, 4]], "rec_scores": []}}],
    ]

    class Model:
        def predict(self, crop):
            return responses.pop(0)

    provider = PaddleOcrProvider(model_factory=lambda **kwargs: Model())
    image = _image(tmp_path / "detections.png")

    assert asyncio.run(provider.detect(image)) == []
    with pytest.raises(ProviderError, match="malformed detections"):
        asyncio.run(provider.detect(image))


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


def test_paddle_array_conversion_normalizes_rgba_to_rgb(monkeypatch) -> None:
    seen = {}

    class Numpy:
        @staticmethod
        def asarray(image):
            seen["mode"] = image.mode
            return "array"

    monkeypatch.setattr("app.services.ocr.paddle_ocr.import_module", lambda _name: Numpy)

    assert _as_array(Image.new("RGBA", (2, 2))) == "array"
    assert seen["mode"] == "RGB"


def test_default_paddle_ocr_uses_one_json_child_and_close_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[dict] = []
    processes = []

    class Stdin:
        def __init__(self, process) -> None:
            self.process = process

        def write(self, line: str) -> None:
            request = json.loads(line)
            requests.append(request)
            self.process.responses.append(
                json.dumps(
                    {
                        "ok": True,
                        "result": {"rec_texts": ["child result"], "rec_scores": [0.9]},
                    }
                )
                + "\n"
            )

        def flush(self) -> None:
            return None

    class Stdout:
        def __init__(self, process) -> None:
            self.process = process

        def readline(self) -> str:
            return self.process.responses.pop(0)

    class Process:
        def __init__(self) -> None:
            self.responses: list[str] = []
            self.stdin = Stdin(self)
            self.stdout = Stdout(self)
            self.returncode = None
            self.terminate_calls = 0
            self.wait_calls = 0

        def poll(self):
            return self.returncode

        def terminate(self) -> None:
            self.terminate_calls += 1
            self.returncode = 0

        def wait(self, timeout=None) -> int:
            self.wait_calls += 1
            return 0

    def popen(*args, **kwargs):
        process = Process()
        processes.append((args, kwargs, process))
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)
    image = _image(tmp_path / "child.png")
    provider = PaddleOcrProvider()

    first = asyncio.run(
        provider.recognize(
            image,
            BoundingBox(x=1, y=2, width=3, height=4),
            SourceLanguage.EN,
        )
    )
    second = asyncio.run(
        provider.recognize(
            image,
            BoundingBox(x=2, y=3, width=4, height=5),
            SourceLanguage.EN,
        )
    )
    provider.close()
    provider.close()

    assert first.source_text == second.source_text == "child result"
    assert len(processes) == 1
    assert all(json.dumps(request) for request in requests)
    assert requests[0]["operation"] == "recognize"
    assert requests[0]["image_path"] == str(image.path)
    assert requests[0]["bbox"] == {"x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0}
    assert processes[0][2].terminate_calls == 1
    assert processes[0][2].wait_calls == 1
    assert "paddleocr" not in sys.modules
    assert "cv2" not in sys.modules


def test_default_paddle_ocr_child_eof_is_recoverable(tmp_path: Path, monkeypatch) -> None:
    class Stdout:
        def readline(self) -> str:
            return ""

    class Process:
        class Stdin:
            def write(self, line: str) -> None:
                return None

            def flush(self) -> None:
                return None

        stdin = Stdin()
        stdout = Stdout()
        returncode = 1

        def poll(self):
            return self.returncode

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return self.returncode

    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: Process())
    provider = PaddleOcrProvider()

    with pytest.raises(ProviderError, match="child") as error:
        asyncio.run(
            provider.recognize(
                _image(tmp_path / "eof.png"),
                BoundingBox(x=0, y=0, width=2, height=2),
                SourceLanguage.EN,
            )
        )

    assert error.value.recoverable is True


def test_paddle_child_redirects_native_stdout_to_stderr(monkeypatch, capsys) -> None:
    class Model:
        def predict(self, image):
            print("native inference log")
            return [{"rec_texts": ["child"], "rec_scores": [0.8]}]

    class PaddleOCR:
        def __init__(self, **kwargs) -> None:
            print("native startup log")

        def predict(self, image):
            return Model().predict(image)

    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=PaddleOCR))
    monkeypatch.setattr(paddle_worker, "_image_input", lambda *_args: object())

    result = paddle_worker._handle(
        {
            "operation": "recognize",
            "image_path": "page.png",
            "bbox": {"x": 0, "y": 0, "width": 2, "height": 2},
            "language": "en",
            "device": "cpu",
        },
        {},
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "native startup log" in captured.err
    assert "native inference log" in captured.err
    assert result == [{"rec_texts": ["child"], "rec_scores": [0.8]}]
