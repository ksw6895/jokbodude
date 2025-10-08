"""Utilities for converting raw Gemini API usage into JokboDude tokens."""

from __future__ import annotations

import math
import os
from functools import lru_cache
from typing import Any, Optional

_PRICE_TABLE = {
    "flash": {
        "input": 0.30 / 1_000_000,
        "output": 2.50 / 1_000_000,
        "cached": 0.075 / 1_000_000,
    },
    "flash-lite": {
        "input": 0.10 / 1_000_000,
        "output": 0.40 / 1_000_000,
        "cached": 0.025 / 1_000_000,
    },
    "pro": {
        "input": 1.25 / 1_000_000,
        "output": 10.00 / 1_000_000,
        "cached": 0.31 / 1_000_000,
    },
}
_PRICE_TABLE["default"] = dict(_PRICE_TABLE["flash"])

_DEFAULT_INPUT_SHARE = 0.75


class BillingConverter:
    """Convert Gemini API usage metrics into JokboDude (JD) tokens using model pricing.

    The converter first estimates the USD cost of a response based on
    Gemini's published per-token pricing (input/output/cached). The total
    cost is then divided by the USD value of one JD token (default 0.002)
    and rounded up. Deployments can adjust the JD ↔ USD ratio by setting
    ``JD_TOKEN_USD_VALUE`` or tweak the per-model multiplier via
    family by setting one of the following environment variables:

    * ``JD_TOKEN_MULTIPLIER_FLASH``
    * ``JD_TOKEN_MULTIPLIER_FLASH_LITE``
    * ``JD_TOKEN_MULTIPLIER_PRO``
    * ``JD_TOKEN_MULTIPLIER_DEFAULT`` (fallback for unknown models)

    The multiplier scales the computed JD charge per model family so operators
    can tune pricing without modifying code. Values are cached to avoid
    recomputing on every call.
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
            total_hint = 0
        else:
            total_hint = int(api_total)

        input_tokens, output_tokens, cached_tokens = BillingConverter._extract_usage_counts(
            total_hint, usage_details
        )

        if input_tokens <= 0 and output_tokens <= 0 and cached_tokens <= 0:
            return 0

        family = BillingConverter._canonical_model_family(model)
        pricing = BillingConverter._pricing_for_family(family)

        input_rate = pricing.get("input", 0.0)
        output_rate = pricing.get("output", input_rate)
        cached_rate = pricing.get("cached", input_rate)

        cost_usd = (
            (input_tokens * input_rate)
            + (output_tokens * output_rate)
            + (cached_tokens * cached_rate)
        )

        if cost_usd <= 0:
            return 0

        usd_per_jd = BillingConverter._usd_per_jd()
        try:
            jd_float = cost_usd / usd_per_jd
        except Exception:
            jd_float = cost_usd / 0.002

        multiplier = BillingConverter._multiplier_for_model(model)
        try:
            converted = math.ceil(jd_float * multiplier)
        except Exception:
            converted = math.ceil(jd_float)

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

    @staticmethod
    @lru_cache(maxsize=1)
    def _usd_per_jd() -> float:
        raw = os.getenv("JD_TOKEN_USD_VALUE")
        if raw is not None:
            try:
                value = float(raw)
                if value > 0:
                    return value
            except Exception:
                pass
        return 0.002

    @staticmethod
    def _pricing_for_family(family: str) -> dict[str, float]:
        return _PRICE_TABLE.get(family, _PRICE_TABLE["default"])

    @staticmethod
    def _extract_usage_counts(
        api_total: int,
        usage_details: Any,
    ) -> tuple[int, int, int]:
        input_tokens = BillingConverter._coerce_int(
            BillingConverter._get_usage_value(
                usage_details,
                (
                    "input_token_count",
                    "prompt_token_count",
                    "input_tokens",
                    "text_input_token_count",
                ),
            )
        )
        output_tokens = BillingConverter._coerce_int(
            BillingConverter._get_usage_value(
                usage_details,
                (
                    "output_token_count",
                    "output_tokens",
                    "candidates_token_count",
                    "generated_token_count",
                ),
            )
        )
        cached_tokens = BillingConverter._coerce_int(
            BillingConverter._get_usage_value(
                usage_details,
                (
                    "cached_content_token_count",
                    "cached_token_count",
                ),
            )
        )

        total_hint = api_total
        total_from_details = BillingConverter._coerce_int(
            BillingConverter._get_usage_value(
                usage_details,
                ("total_token_count",),
            )
        )
        if total_from_details > total_hint:
            total_hint = total_from_details

        known = input_tokens + output_tokens + cached_tokens

        if total_hint > 0:
            if known == 0:
                estimated_input = max(
                    0,
                    min(total_hint, int(round(total_hint * _DEFAULT_INPUT_SHARE))),
                )
                input_tokens = estimated_input
                output_tokens = max(0, total_hint - estimated_input)
            elif known < total_hint:
                remainder = total_hint - known
                input_tokens += remainder

        return max(0, input_tokens), max(0, output_tokens), max(0, cached_tokens)

    @staticmethod
    def _get_usage_value(source: Any, candidates: tuple[str, ...]) -> Any:
        if source is None:
            return None
        if isinstance(source, dict):
            for key in candidates:
                if key in source:
                    return source[key]
        for key in candidates:
            try:
                if hasattr(source, key):
                    return getattr(source, key)
            except Exception:
                continue
        return None

    @staticmethod
    def _coerce_int(value: Any) -> int:
        if value is None:
            return 0
        try:
            if isinstance(value, bool):
                return int(value)
            return int(value)
        except Exception:
            try:
                return int(float(value))
            except Exception:
                return 0
