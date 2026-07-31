"""Source-language override resolution."""

from app.core.models import Page, ProjectSettings, SourceLanguage, TextBlock


def resolve_source_language(
    settings: ProjectSettings,
    page: Page | None = None,
    block: TextBlock | None = None,
) -> SourceLanguage:
    """Resolve Block > Page > Project without inferring from layout or text."""
    if block is not None and block.source_language is not None:
        return block.source_language
    if page is not None and page.source_language is not None:
        return page.source_language
    return settings.default_source_language
