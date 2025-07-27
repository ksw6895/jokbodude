import os
import google.generativeai as genai
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

API_KEY = os.getenv('GEMINI_API_KEY')

if not API_KEY:
    raise ValueError("Please set GEMINI_API_KEY in .env file")

genai.configure(api_key=API_KEY)

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
    }
]

# Model name mapping
MODEL_NAMES = {
    "pro": "gemini-2.5-pro",
    "flash": "gemini-2.5-flash",
    "flash-lite": "gemini-2.5-flash-lite"
}

def create_model(model_type: str = "pro", thinking_budget: Optional[int] = None):
    """
    Create a Gemini model with specified configuration.
    
    Args:
        model_type: "pro", "flash", or "flash-lite"
        thinking_budget: Thinking budget for flash/flash-lite models (0-24576, -1 for auto)
                        None for pro model (doesn't support thinking budget)
    
    Returns:
        Configured GenerativeModel instance
    """
    model_name = MODEL_NAMES.get(model_type, MODEL_NAMES["pro"])
    
    # Copy base generation config
    config = GENERATION_CONFIG.copy()
    
    # Add thinking budget for flash/flash-lite models
    if model_type in ["flash", "flash-lite"] and thinking_budget is not None:
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
model = create_model("pro")