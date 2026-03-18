"""Language detection from HTTP headers."""

from typing import Annotated, Literal

from fastapi import Depends, Header

Language = Literal["ru", "uz"]


def get_language(
    accept_language: str = Header(default="ru", alias="Accept-Language"),
    x_language: str | None = Header(default=None, alias="X-Language"),
) -> Language:
    """Get preferred language from headers.

    Priority:
    1. X-Language header (explicit override)
    2. Accept-Language header
    3. Default: "ru"
    """
    # X-Language takes precedence (explicit override from Mini App)
    if x_language and x_language.lower() in ("ru", "uz"):
        return x_language.lower()  # type: ignore[return-value]

    # Parse Accept-Language (simplified)
    if accept_language:
        lang = accept_language.lower()
        if "uz" in lang or "o'zbek" in lang:
            return "uz"

    return "ru"


CurrentLanguage = Annotated[Language, Depends(get_language)]
