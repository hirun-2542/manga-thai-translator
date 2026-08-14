"""PaddleOCR child process; keep native OCR imports out of the Qt process."""

import json
import sys
from collections.abc import Mapping
from contextlib import redirect_stdout
from math import ceil, floor
from pathlib import Path
from typing import Any


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return _json_value(tolist())
    item = getattr(value, "item", None)
    if callable(item):
        return _json_value(item())
    return str(value)


def _mapping_result(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping) and hasattr(value, "json"):
        payload = value.json() if callable(value.json) else value.json
        if isinstance(payload, str):
            payload = json.loads(payload)
        value = payload
    if isinstance(value, Mapping) and isinstance(value.get("res"), Mapping):
        value = value["res"]
    if isinstance(value, Mapping):
        return value
    return None


def _result_entries(raw: object) -> list[object]:
    if isinstance(raw, (str, bytes, Mapping)):
        return [raw]
    try:
        return list(raw)  # type: ignore[arg-type]
    except TypeError:
        return [raw]


def _normalize(raw: object, fields: tuple[str, str]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for entry in _result_entries(raw):
        mapping = _mapping_result(entry)
        result: dict[str, object] = {}
        for field in fields:
            value = mapping.get(field) if mapping is not None else getattr(entry, field, None)
            result[field] = _json_value(value)
        entries.append(result)
    return entries


def _image_input(path: Path, bbox: Mapping[str, object] | None) -> Any:
    import cv2

    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot read image: {path}")
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    if bbox is None:
        return image
    x = float(bbox["x"])
    y = float(bbox["y"])
    width = float(bbox["width"])
    height = float(bbox["height"])
    left = max(0, floor(x))
    top = max(0, floor(y))
    right = min(image.shape[1], ceil(x + width))
    bottom = min(image.shape[0], ceil(y + height))
    if right <= left or bottom <= top:
        raise ValueError("empty OCR crop")
    return image[top:bottom, left:right]


def _handle(request: Mapping[str, object], models: dict[str, Any]) -> object:
    with redirect_stdout(sys.stderr):
        from paddleocr import PaddleOCR

        language = str(request["language"])
        model = models.get(language)
        if model is None:
            model = PaddleOCR(
                lang=language,
                device=str(request.get("device", "cpu")),
                enable_mkldnn=False,
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
            )
            models[language] = model
        path = Path(str(request["image_path"]))
        operation = request.get("operation")
        bbox = request.get("bbox") if operation == "recognize" else None
        raw = model.predict(_image_input(path, bbox if isinstance(bbox, Mapping) else None))
        fields = (
            ("rec_texts", "rec_scores") if operation == "recognize" else ("rec_boxes", "rec_scores")
        )
        return _normalize(raw, fields)


def main() -> None:
    models: dict[str, Any] = {}
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if not isinstance(request, Mapping):
                raise ValueError("request must be an object")
            result = _handle(request, models)
            response = {"ok": True, "result": result}
        except Exception as error:
            response = {"ok": False, "error": f"{type(error).__name__}: {error}"}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
