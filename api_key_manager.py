"""
API Key Manager for handling multiple Gemini API keys
"""
import time
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import threading
import google.generativeai as genai
from config import API_KEYS, GENERATION_CONFIG, SAFETY_SETTINGS, MODEL_NAMES


class APIKeyManager:
    """Manages multiple Gemini API keys with rate limit handling"""
    
    def __init__(self, api_keys: List[str], model_type: str = "pro", thinking_budget: Optional[int] = None):
        """
        Initialize API Key Manager with multiple API keys
        
        Args:
            api_keys: List of Gemini API keys
            model_type: Model type to use ("pro", "flash", "flash-lite")
            thinking_budget: Thinking budget for flash models
        """
        self.api_keys = api_keys
        self.model_type = model_type
        self.thinking_budget = thinking_budget
        self.model_name = MODEL_NAMES.get(model_type, MODEL_NAMES["pro"])
        
        # Track API key states
        self.api_states: Dict[str, Dict] = {}
        for key in api_keys:
            self.api_states[key] = {
                'available': True,
                'cooldown_until': None,
                'genai_client': None,
                'model': None,
                'usage_count': 0,
                'last_used': None
            }
        
        # Round-robin index
        self.current_index = 0
        self.lock = threading.Lock()
        
        # Initialize all API clients
        self._initialize_clients()
    
    def _initialize_clients(self):
        """Initialize genai clients for all API keys"""
        for api_key in self.api_keys:
            try:
                # Create a new genai instance for each API key
                # Note: genai.configure is global, so we need to work around this
                # Each thread will configure before use
                self.api_states[api_key]['genai_client'] = api_key  # Store key for later configuration
                
                # We'll create models on-demand when needed
            except Exception as e:
                print(f"Failed to initialize API key: {str(e)[:20]}...")
                self.api_states[api_key]['available'] = False
    
    def get_available_api(self) -> Optional[Tuple[str, genai.GenerativeModel]]:
        """
        Get an available API key and its model using round-robin selection
        
        Returns:
            Tuple of (api_key, model) or None if no API is available
        """
        with self.lock:
            # Check cooldown status for all APIs
            current_time = datetime.now()
            for api_key, state in self.api_states.items():
                if state['cooldown_until'] and current_time >= state['cooldown_until']:
                    state['available'] = True
                    state['cooldown_until'] = None
                    print(f"  API key ending with ...{api_key[-4:]} is now available again")
            
            # Try to find an available API starting from current index
            attempts = 0
            while attempts < len(self.api_keys):
                api_key = self.api_keys[self.current_index]
                state = self.api_states[api_key]
                
                if state['available']:
                    # Configure genai with this API key
                    genai.configure(api_key=api_key)
                    
                    # Create model if not exists
                    if state['model'] is None:
                        config = GENERATION_CONFIG.copy()
                        state['model'] = genai.GenerativeModel(
                            model_name=self.model_name,
                            generation_config=config,
                            safety_settings=SAFETY_SETTINGS,
                        )
                    
                    # Update usage stats
                    state['usage_count'] += 1
                    state['last_used'] = datetime.now()
                    
                    # Move to next index for round-robin
                    self.current_index = (self.current_index + 1) % len(self.api_keys)
                    
                    print(f"  Using API key ending with ...{api_key[-4:]} (usage count: {state['usage_count']})")
                    return api_key, state['model']
                
                # Try next API
                self.current_index = (self.current_index + 1) % len(self.api_keys)
                attempts += 1
            
            # No available API found
            print("  Warning: No available API keys")
            return None
    
    def mark_api_rate_limited(self, api_key: str, cooldown_minutes: int = 10):
        """
        Mark an API key as rate limited and set cooldown period
        
        Args:
            api_key: The API key that hit rate limit
            cooldown_minutes: Cooldown period in minutes
        """
        with self.lock:
            if api_key in self.api_states:
                self.api_states[api_key]['available'] = False
                self.api_states[api_key]['cooldown_until'] = datetime.now() + timedelta(minutes=cooldown_minutes)
                print(f"  API key ending with ...{api_key[-4:]} marked as rate limited. Cooldown for {cooldown_minutes} minutes")
    
    def get_status(self) -> Dict[str, Dict]:
        """Get current status of all API keys"""
        with self.lock:
            status = {}
            current_time = datetime.now()
            
            for api_key, state in self.api_states.items():
                status[f"...{api_key[-4:]}"] = {
                    'available': state['available'],
                    'usage_count': state['usage_count'],
                    'last_used': state['last_used'].strftime('%H:%M:%S') if state['last_used'] else 'Never',
                    'cooldown_remaining': None
                }
                
                if state['cooldown_until'] and state['cooldown_until'] > current_time:
                    remaining = state['cooldown_until'] - current_time
                    status[f"...{api_key[-4:]}"]['cooldown_remaining'] = f"{remaining.seconds // 60}m {remaining.seconds % 60}s"
            
            return status
    
    def reset_api_key(self, api_key: str):
        """Reset a specific API key to available state"""
        with self.lock:
            if api_key in self.api_states:
                self.api_states[api_key]['available'] = True
                self.api_states[api_key]['cooldown_until'] = None
                print(f"  API key ending with ...{api_key[-4:]} has been reset to available")