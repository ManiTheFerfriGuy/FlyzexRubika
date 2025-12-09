from __future__ import annotations

from typing import Callable


class _Filter:
    def __init__(self, func: Callable[["Update"], bool]):
        self.func = func

    def __call__(self, update: "Update") -> bool:
        return self.func(update)

    def __and__(self, other: "_Filter") -> "_Filter":
        return _Filter(lambda update: self(update) and other(update))

    def __or__(self, other: "_Filter") -> "_Filter":
        return _Filter(lambda update: self(update) or other(update))

    def __invert__(self) -> "_Filter":
        return _Filter(lambda update: not self(update))


class _ChatTypeFilters:
    PRIVATE = _Filter(lambda update: getattr(update.effective_chat, "type", "private") == "private")
    GROUPS = _Filter(lambda update: getattr(update.effective_chat, "type", "group") == "group")


class _Filters:
    ChatType = _ChatTypeFilters
    TEXT = _Filter(lambda update: bool(getattr(update.effective_message, "text", "")))
    COMMAND = _Filter(lambda update: bool(getattr(update.effective_message, "text", "").startswith("/")))


filters = _Filters()
