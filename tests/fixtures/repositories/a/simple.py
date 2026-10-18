"""Simple fixture A: functions, a class, and resolvable internal calls."""

from __future__ import annotations


def add(x: int, y: int) -> int:
    return x + y


def double(value: int) -> int:
    """Call a sibling function (resolvable statically)."""
    return add(value, value)


class Greeter:
    """A small class with a method that calls a module function."""

    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return add(len(self.name), 1).__str__()
