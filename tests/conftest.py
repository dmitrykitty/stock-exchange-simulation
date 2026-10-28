"""Shared test helpers.

Contract tests run against implementations that may not exist yet. ``skip_if_not_implemented`` turns a
NotImplementedError into a skip, so a skeleton gives "skipped", and a finished implementation gives
"passed" or "failed".
"""

from __future__ import annotations

import functools

import pytest


def skip_if_not_implemented(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except NotImplementedError as exc:
            pytest.skip(f"not implemented yet: {exc}")

    return wrapper
