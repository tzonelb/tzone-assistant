"""The extension bus.

Modules extend each other only through named hooks — never by importing one another's
internals. A module that wants to react to something calls `hooks.on(...)`; a module that wants
to be extended calls `hooks.emit(...)` or `hooks.collect(...)`. Adding a listener never requires
editing the emitter, which is what lets a hundred modules coexist.

Handlers run in ascending `sequence`, so a later module can deliberately run before or after an
earlier one without either knowing about the other.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(order=True)
class _Handler:
    sequence: int
    order: int
    module: str = field(compare=False)
    fn: Callable[..., Any] = field(compare=False)


class HookBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[_Handler]] = {}
        self._counter = 0
        self._current_module = "core"

    def binding_module(self, module_key: str) -> None:
        """Set by the registry while a module is being loaded, for diagnostics."""
        self._current_module = module_key

    def on(self, name: str, fn: Callable[..., Any], sequence: int = 10) -> None:
        self._counter += 1
        self._handlers.setdefault(name, []).append(
            _Handler(sequence=sequence, order=self._counter, module=self._current_module, fn=fn)
        )
        self._handlers[name].sort()

    def emit(self, name: str, **kwargs: Any) -> None:
        """Fire-and-forget notification. Return values are ignored."""
        for handler in self._handlers.get(name, []):
            handler.fn(**kwargs)

    def collect(self, name: str, **kwargs: Any) -> list[Any]:
        """Gather one contribution per handler, dropping `None`."""
        results = []
        for handler in self._handlers.get(name, []):
            value = handler.fn(**kwargs)
            if value is not None:
                results.append(value)
        return results

    def chain(self, name: str, value: Any, **kwargs: Any) -> Any:
        """Pass a value through every handler, each returning the next value.

        Used where modules refine a shared result — e.g. enriching a pushed record before it
        is written.
        """
        for handler in self._handlers.get(name, []):
            value = handler.fn(value, **kwargs)
        return value

    def describe(self) -> dict[str, list[str]]:
        return {
            name: [f"{h.module} (seq {h.sequence})" for h in handlers]
            for name, handlers in sorted(self._handlers.items())
        }
