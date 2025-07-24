import os
import google.generativeai as genai
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv('GEMINI_API_KEY')

if not API_KEY:
    raise ValueError("Please set GEMINI_API_KEY in .env file")

genai.configure(api_key=API_KEY)

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

model = genai.GenerativeModel(
    model_name="gemini-2.5-pro",
    generation_config=GENERATION_CONFIG,
    safety_settings=SAFETY_SETTINGS,
)