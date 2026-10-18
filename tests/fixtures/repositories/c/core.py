"""Fixture C: single- and multi-level inheritance resolution."""

from __future__ import annotations


class Animal:
    def speak(self) -> str:
        return "?"


class Dog(Animal):
    def speak(self) -> str:
        return "woof"


class GuideDog(Dog):
    def guide(self) -> str:
        return self.speak()
