from pathlib import Path

import pytest

from app.services import system_fonts


class FakeCompleted:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_resolve_system_font_keeps_static_ttc_face_index(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        system_fonts.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompleted("Noto Serif CJK SC\tBold\t/tmp/font.ttc\t2\n"),
    )
    monkeypatch.setattr(system_fonts, "load_pillow_font", lambda *_args: object())

    resolved = system_fonts.resolve_system_font("Noto Serif CJK SC", "Bold")

    assert resolved.path == Path("/tmp/font.ttc")
    assert resolved.face_index == 2
    assert resolved.variation_name is None


def test_resolve_system_font_decodes_variable_named_instance_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_fonts.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompleted("Dosis\tBold\t/tmp/dosis.ttf\t393219\n"),
    )
    monkeypatch.setattr(system_fonts, "load_pillow_font", lambda *_args: object())

    resolved = system_fonts.resolve_system_font("Dosis", "Bold")

    assert resolved.face_index == 3
    assert resolved.variation_name == "Bold"


def test_resolve_system_font_rejects_fontconfig_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        system_fonts.subprocess,
        "run",
        lambda *args, **kwargs: FakeCompleted("Noto Sans\tRegular\t/tmp/fallback.ttf\t0\n"),
    )

    with pytest.raises(system_fonts.FontResolutionError, match="fallback"):
        system_fonts.resolve_system_font("Noto Sans Thai", "Regular")


def test_load_pillow_font_applies_named_variable_style(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeFont:
        def __init__(self) -> None:
            self.styles: list[str] = []

        def set_variation_by_name(self, style: str) -> None:
            self.styles.append(style)

    loaded = FakeFont()
    monkeypatch.setattr(system_fonts.ImageFont, "truetype", lambda *args, **kwargs: loaded)
    font = system_fonts.SystemFont(
        "Dosis",
        "Bold",
        Path("/tmp/dosis.ttf"),
        face_index=3,
        variation_name="Bold",
    )

    assert system_fonts.load_pillow_font(font, 12) is loaded
    assert loaded.styles == ["Bold"]
