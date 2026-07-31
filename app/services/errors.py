"""Recoverable provider failures."""


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, recoverable: bool) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class UnsupportedLanguageError(ProviderError):
    def __init__(self, provider: str, language: str) -> None:
        super().__init__(
            f"{provider} does not support unresolved or unknown language {language!r}",
            recoverable=True,
        )
        self.provider = provider
        self.language = language
