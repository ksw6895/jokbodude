"""Utilities for converting raw Gemini API usage into JokboDude tokens."""

from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Any, Optional


class BillingConverter:
    """Convert Gemini API usage metrics into JokboDude (JD) tokens.

    The converter applies a per-model multiplier that is tuned via
    environment variables. By default **100 Gemini tokens** map to **1 JD
    token** so that small oscillations in prompt size do not immediately
    deduct user balances. Deployments can override the multiplier per model
    family by setting one of the following environment variables:

    * ``JD_TOKEN_MULTIPLIER_FLASH``
    * ``JD_TOKEN_MULTIPLIER_FLASH_LITE``
    * ``JD_TOKEN_MULTIPLIER_PRO``
    * ``JD_TOKEN_MULTIPLIER_DEFAULT`` (fallback for unknown models)

    The multiplier represents the number of JD tokens charged for every 100
    Gemini tokens observed in ``usage_metadata.total_token_count``. The values
    are cached to avoid recomputing on every call.
    """

    @staticmethod
    def api_to_jd(
        api_total: int,
        model: Optional[str],
        usage_details: Any = None,
    ) -> int:
        """Convert a Gemini token count into JD tokens.

        Args:
            api_total: The ``usage_metadata.total_token_count`` value reported
                by Gemini. When ``None`` or non-positive the conversion yields
                ``0``.
            model: The model identifier used for the request. Only the primary
                family (``flash``, ``flash-lite``, ``pro``) is inspected when
                looking up multipliers.
            usage_details: Optional structure with richer usage metadata. It is
                accepted for future extensions (e.g. cache discounts) but is
                currently unused.

        Returns:
            The converted JD token charge rounded up to the nearest integer.
        """

        if not api_total or api_total <= 0:
            return 0

        multiplier = BillingConverter._multiplier_for_model(model)
        try:
            converted = math.ceil((float(api_total) / 100.0) * multiplier)
        except Exception:
            converted = math.ceil(float(api_total) / 100.0)

        return max(0, int(converted))

    @staticmethod
    def _multiplier_for_model(model: Optional[str]) -> float:
        family = BillingConverter._canonical_model_family(model)
        multipliers = BillingConverter._load_multipliers()
        return multipliers.get(family, multipliers.get("default", 1.0)) or 1.0

    @staticmethod
    def _canonical_model_family(model: Optional[str]) -> str:
        if not model:
            return "default"
        normalized = str(model).strip().lower()
        if not normalized:
            return "default"
        if "flash-lite" in normalized or normalized.endswith("lite"):
            return "flash-lite"
        if "pro" in normalized:
            return "pro"
        if "flash" in normalized:
            return "flash"
        return "default"

    @staticmethod
    @lru_cache(maxsize=1)
    def _load_multipliers() -> dict[str, float]:
        def _env_multiplier(name: str, default: float) -> float:
            raw = os.getenv(name)
            if raw is None:
                return default
            try:
                value = float(raw)
                if value <= 0:
                    return default
                return value
            except Exception:
                return default

        default_multiplier = _env_multiplier("JD_TOKEN_MULTIPLIER_DEFAULT", 1.0)
        return {
            "default": default_multiplier,
            "flash": _env_multiplier("JD_TOKEN_MULTIPLIER_FLASH", default_multiplier),
            "flash-lite": _env_multiplier("JD_TOKEN_MULTIPLIER_FLASH_LITE", default_multiplier),
            "pro": _env_multiplier("JD_TOKEN_MULTIPLIER_PRO", default_multiplier),
        }

