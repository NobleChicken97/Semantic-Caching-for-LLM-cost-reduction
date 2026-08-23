"""Shared test fixtures for the semantic cache proxy."""

from __future__ import annotations

import os

import pytest

os.environ["MOCK_LLM"] = "true"


@pytest.fixture(autouse=True)
def _reset_inflight_locks():
    """Clear the chat route's coalescing-lock registry between tests so
    locks created on one test's event loop can never leak into another's."""
    yield
    from proxy.routes import chat as chat_module

    chat_module._inflight_locks.clear()