import os
import google.generativeai as genai
from dotenv import load_dotenv
from typing import Optional, List

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

# Configure with the default (first) API key
# NOTE: Configure is now done explicitly when needed, not at import time
# genai.configure(api_key=API_KEY)

# Base configuration
GENERATION_CONFIG = {
    "temperature": 0.3,
    "top_p": 0.95,
    "top_k": 40,
    "max_output_tokens": 100000,
    "response_mime_type": "application/json",
}

SAFETY_SETTINGS = [
    {
        "category": "HARM_CATEGORY_HARASSMENT",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_HATE_SPEECH",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
        "threshold": "BLOCK_NONE"
    },
    {
        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
        "threshold": "BLOCK_NONE"
    },
    # Ensure civic integrity prompts are not blocked by default
    {
        "category": "HARM_CATEGORY_CIVIC_INTEGRITY",
        "threshold": "BLOCK_NONE"
    }
]

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
    
    return genai.GenerativeModel(
        model_name=model_name,
        generation_config=config,
        safety_settings=SAFETY_SETTINGS,
    )

# Default model (for backward compatibility)
# NOTE: Model creation is now done after explicit configuration
# model = create_model("flash")

def configure_api(api_key: Optional[str] = None):
    """Explicitly configure the API with the given key or default"""
    key_to_use = api_key or API_KEY
    genai.configure(api_key=key_to_use)
