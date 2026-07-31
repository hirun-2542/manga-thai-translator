"""Explicit OCR provider registration."""

from collections.abc import Iterable

from app.services.ocr.base import OcrCapabilities, OcrProvider


class OcrProviderRegistry:
    def __init__(self, providers: Iterable[OcrProvider] = ()) -> None:
        self._providers: dict[str, OcrProvider] = {}
        for provider in providers:
            self.register(provider.name, provider)

    def register(self, name: str, provider: OcrProvider) -> None:
        if not name or name != name.strip():
            raise ValueError("OCR provider name must be non-empty and trimmed")
        if provider.name != name:
            raise ValueError(
                f"OCR provider registration name {name!r} does not match {provider.name!r}"
            )
        if not isinstance(provider.capabilities, OcrCapabilities):
            raise TypeError(f"OCR provider {name!r} has invalid capabilities")
        if name in self._providers:
            raise ValueError(f"OCR provider {name!r} is already registered")
        self._providers[name] = provider

    def get(self, name: str) -> OcrProvider:
        try:
            return self._providers[name]
        except KeyError:
            raise KeyError(f"OCR provider {name!r} is not registered") from None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._providers))

    @property
    def providers(self) -> tuple[OcrProvider, ...]:
        return tuple(self._providers[name] for name in self.names)
