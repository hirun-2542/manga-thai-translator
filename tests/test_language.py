from uuid import uuid4

from app.core.language import resolve_source_language
from app.core.models import (
    BoundingBox,
    Page,
    ProjectSettings,
    SourceLanguage,
    TextBlock,
)


def test_source_language_override_precedence() -> None:
    settings = ProjectSettings(default_source_language=SourceLanguage.JA)
    page = Page(
        source_path="page.png",
        width=100,
        height=100,
        source_language=SourceLanguage.KO,
    )
    block = TextBlock(
        page_id=page.id,
        bbox=BoundingBox(x=0, y=0, width=10, height=10),
        reading_order=0,
        source_language=SourceLanguage.ZH_HANT,
    )

    assert resolve_source_language(settings) is SourceLanguage.JA
    assert resolve_source_language(settings, page) is SourceLanguage.KO
    assert resolve_source_language(settings, page, block) is SourceLanguage.ZH_HANT


def test_resolution_does_not_infer_language() -> None:
    settings = ProjectSettings(default_source_language=SourceLanguage.AUTO)
    page = Page(
        id=uuid4(),
        source_path="日本語-page.png",
        width=100,
        height=100,
    )

    assert resolve_source_language(settings, page) is SourceLanguage.AUTO
