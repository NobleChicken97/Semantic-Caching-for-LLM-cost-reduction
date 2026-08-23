"""Shared test fixtures for the semantic cache proxy."""

from __future__ import annotations

import os

os.environ["MOCK_LLM"] = "true"