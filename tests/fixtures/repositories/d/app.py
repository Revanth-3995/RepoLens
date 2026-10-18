"""Fixture D: relationships that cannot be conservatively resolved."""

import os
import unknown_package

from . import missing_sibling


def call_external():
    """stdlib call -> external."""
    return os.path.join("a", "b")


def call_unknown():
    """unknown module call -> external/unresolved, never guessed."""
    return unknown_package.func()


def call_dynamic(obj):
    """attribute call on an arbitrary parameter -> dynamic_or_unknown."""
    return obj.method()


def use_getattr(obj):
    """getattr dispatch -> dynamic_or_unknown."""
    fn = getattr(obj, "run", None)
    return fn()
