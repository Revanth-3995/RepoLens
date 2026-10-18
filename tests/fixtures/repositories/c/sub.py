"""Fixture C: cross-module inheritance."""

from core import Animal


class Robot(Animal):
    def speak(self) -> str:
        return "beep"
