import os
from google import genai  # google-genai unified SDK
from dotenv import load_dotenv
from typing import Optional, List, Dict, Any

load_dotenv()

API_KEY = os.getenv('GEMINI_API_KEY')
API_KEYS_STR = os.getenv('GEMINI_API_KEYS')

# Support both single key and multiple keys
if API_KEYS_STR:
    # Parse comma-separated API keys
    API_KEYS = [key.strip() for key in API_KEYS_STR.split(',') if key.strip()]
    if not API_KEYS:
        raise ValueError("GEMINI_API_KEYS is empty or invalid")
    # Use the first key as default
    API_KEY = API_KEYS[0]
elif API_KEY:
    # Fallback to single key
    API_KEYS = [API_KEY]
else:
    raise ValueError("Please set either GEMINI_API_KEY or GEMINI_API_KEYS in .env file")

# Note: google-genai prefers per-instance clients over global configure.
# We avoid global state and create genai.Client(...) where needed.

# Base configuration
GENERATION_CONFIG = {
    "temperature": 0.3,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 100000,
    "response_mime_type": "application/json",
}

# Base desired safety configuration (may be filtered if SDK lacks support)
def _truthy(val: Optional[str]) -> bool:
    if val is None:
        return False
    return str(val).strip().lower() in ("1", "true", "t", "yes", "y", "on")

# Default: disable safety filters unless explicitly overridden
DISABLE_SAFETY_FILTERS = _truthy(os.getenv("DISABLE_SAFETY_FILTERS", "true"))

SAFETY_SETTINGS: Optional[List[Dict[str, Any]]] = None  # computed on demand

def _build_block_none_safety_settings() -> List[Dict[str, Any]] | list:
    """Best-effort: build safety settings that disable blocking.

    Tries the new google-genai typed API first; falls back to dict form.
    Returns a list suitable for passing as `safety_settings`.
    """
    categories = [
        "HARM_CATEGORY_HARASSMENT",
        "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "HARM_CATEGORY_DANGEROUS_CONTENT",
        "HARM_CATEGORY_CIVIC_INTEGRITY",
    ]
    # Try typed API
    try:
        from google.genai import types as _types  # type: ignore
        HC = getattr(_types, "HarmCategory", None)
        BT = getattr(_types, "BlockThreshold", None)
        SS = getattr(_types, "SafetySetting", None)
        if HC and BT and SS:
            out = []
            for name in categories:
                cat = getattr(HC, name, None) or name
                thr = getattr(BT, "BLOCK_NONE", None) or "BLOCK_NONE"
                out.append(SS(category=cat, threshold=thr))
            return out
    except Exception:
        pass
    # Fallback: dicts
    return [{"category": name, "threshold": "BLOCK_NONE"} for name in categories]


def get_safety_settings() -> Optional[List[Dict[str, Any]]]:
    """Return safety settings, honoring DISABLE_SAFETY_FILTERS.

    - If DISABLE_SAFETY_FILTERS is true (default), returns a best-effort list
      that disables blocking (BLOCK_NONE per category).
    - If false, returns None to rely on server defaults.
    """
    if DISABLE_SAFETY_FILTERS:
        try:
            return _build_block_none_safety_settings()  # type: ignore[return-value]
        except Exception:
            return None
    return None

# Model name mapping
MODEL_NAMES = {
    "flash": "gemini-2.5-flash",
    "pro": "gemini-2.5-pro",
}

def create_model(model_type: str = "flash", thinking_budget: Optional[int] = None):
    """
    Create a Gemini model with specified configuration.
    
    Args:
        model_type: "flash" (default) or "pro"
        thinking_budget: Optional thinking budget for flash model (0-24576, -1 for auto)
    
    Returns:
        Configured GenerativeModel instance
    """
    model_name = MODEL_NAMES.get(model_type, MODEL_NAMES["flash"])
    
    # Copy base generation config
    config = GENERATION_CONFIG.copy()
    
    # Add thinking budget for flash model
    if model_type in ["flash"] and thinking_budget is not None:
        # Note: The thinking_config parameter may need to be passed differently
        # depending on the exact API version. This is a placeholder implementation.
        # The actual implementation might require using a different parameter name
        # or passing it through a different method.
        print(f"Note: Thinking budget {thinking_budget} will be used for {model_name}")
        # TODO: Add actual thinking budget configuration when API supports it
        # config["thinking_budget"] = thinking_budget
    
    # Bind a default client so the model is fully scoped without global config
    # New SDK does not require a GenerativeModel instance; we return a config dict.
    return {
        "model_name": model_name,
        "generation_config": config,
        "safety_settings": get_safety_settings(),
        # Informational only; clients will instantiate `genai.Client` per key
        "_api_key_bound": API_KEY is not None,
    }

# Default model (for backward compatibility)
# NOTE: Model creation is now done after explicit configuration
# model = create_model("flash")

def configure_api(api_key: Optional[str] = None):
    """Create and return a scoped google-genai Client.

    New SDK does not require global configuration. This helper returns a
    `genai.Client` for convenience and backward compatibility with callers.
    """
    key_to_use = api_key or API_KEY
    return genai.Client(api_key=key_to_use)
