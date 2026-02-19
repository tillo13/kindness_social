#!/usr/bin/env python3
"""
LM Studio Utilities - HTTP API Management with minimal SSH support
Handles HTTP API connections and model operations for LM Studio.
"""

import requests
import json
import subprocess
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass

@dataclass
class ServerConfig:
    """Configuration for LM Studio server connection."""
    host: str
    port: int = 1234
    ssh_user: str = None
    timeout: int = 120

@dataclass
class ModelInfo:
    """Information about an available model."""
    id: str
    type: str
    publisher: str
    state: str
    max_context: int
    quantization: str = None
    loaded_context: int = None

# Default model configuration
DEFAULT_MODEL_CONFIG = {
    "temperature": 0.7,
    "max_tokens": 1000,
    "stream": True,
    "top_p": 0.9,
    "top_k": 40,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "stop": None,
    "seed": None,
}

# Default system prompt
DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant."

class LMStudioUtils:
    """Utility class for managing LM Studio server via HTTP API with minimal SSH support."""
    
    def __init__(self, config: ServerConfig):
        self.config = config
        self.base_url = f"http://{config.host}:{config.port}"
        self.models_endpoint = f"{self.base_url}/api/v0/models"
        self.chat_endpoint = f"{self.base_url}/api/v0/chat/completions"
        
    def check_server_connection(self) -> bool:
        """Check if LM Studio server is reachable via HTTP."""
        try:
            response = requests.get(self.models_endpoint, timeout=5)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False
    
    def check_ssh_connection(self) -> bool:
        """Check if SSH connection is working."""
        if not self.config.ssh_user:
            return False
        
        try:
            ssh_cmd = ["ssh", f"{self.config.ssh_user}@{self.config.host}", "echo 'SSH OK'"]
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=10)
            return result.returncode == 0
        except Exception:
            return False
    
    def start_server(self) -> Tuple[bool, str]:
        """Start the LM Studio server via SSH."""
        if not self.config.ssh_user:
            return False, "SSH not configured"
        
        try:
            ssh_cmd = ["ssh", f"{self.config.ssh_user}@{self.config.host}", "lms server start"]
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return True, "Server start command sent"
            else:
                return False, f"SSH command failed: {result.stderr}"
        except Exception as e:
            return False, f"SSH error: {e}"
    
    def stop_server(self) -> Tuple[bool, str]:
        """Stop the LM Studio server via SSH."""
        if not self.config.ssh_user:
            return False, "SSH not configured"
        
        try:
            ssh_cmd = ["ssh", f"{self.config.ssh_user}@{self.config.host}", "lms server stop"]
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return True, "Server stop command sent"
            else:
                return False, f"SSH command failed: {result.stderr}"
        except Exception as e:
            return False, f"SSH error: {e}"
    
    def load_model(self, model_id: str) -> Tuple[bool, str]:
        """Load a model via SSH."""
        if not self.config.ssh_user:
            return False, "SSH not configured"
        
        try:
            ssh_cmd = ["ssh", f"{self.config.ssh_user}@{self.config.host}", f'lms load "{model_id}" -y']
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                return True, f"Load command sent for {model_id}"
            else:
                return False, f"Load command failed: {result.stderr}"
        except Exception as e:
            return False, f"SSH error: {e}"
    
    def get_available_models(self) -> List[ModelInfo]:
        """Get list of available models from LM Studio."""
        try:
            response = requests.get(self.models_endpoint, timeout=10)
            if response.status_code == 200:
                data = response.json()
                models = []
                for model_data in data.get("data", []):
                    models.append(ModelInfo(
                        id=model_data["id"],
                        type=model_data.get("type", "unknown"),
                        publisher=model_data.get("publisher", "unknown"),
                        state=model_data.get("state", "unknown"),
                        max_context=model_data.get("max_context_length", 0),
                        quantization=model_data.get("quantization"),
                        loaded_context=model_data.get("loaded_context_length")
                    ))
                return models
        except requests.exceptions.RequestException:
            pass
        return []
    
    def get_loaded_models(self) -> List[ModelInfo]:
        """Get list of currently loaded models."""
        models = self.get_available_models()
        return [m for m in models if m.state == "loaded"]
    
    def get_connection_info(self) -> Dict:
        """Get comprehensive connection and status information."""
        info = {
            "server_url": self.base_url,
            "http_accessible": self.check_server_connection(),
            "models": self.get_available_models(),
            "loaded_models": self.get_loaded_models()
        }
        return info
    
    def ensure_server_ready(self) -> Tuple[bool, str]:
        """Check if server is reachable and has models available."""
        # Check if server is reachable
        if not self.check_server_connection():
            return False, "Server not reachable via HTTP API"
        
        # Check if any models are available
        available_models = self.get_available_models()
        if not available_models:
            return False, "No models available on server"
        
        return True, "Server is ready and accessible"
    
    def get_model_by_id(self, model_id: str) -> Optional[ModelInfo]:
        """Get specific model information by ID."""
        models = self.get_available_models()
        for model in models:
            if model.id == model_id:
                return model
        return None
    
    def is_model_loaded(self, model_id: str) -> bool:
        """Check if a specific model is currently loaded."""
        model = self.get_model_by_id(model_id)
        return model is not None and model.state == "loaded"
    
    def send_chat_request(self, messages: List[Dict], model_config: Dict, model_id: str) -> Tuple[bool, any]:
        """Send a chat request to the LM Studio server."""
        try:
            # Prepare payload
            payload = {
                "model": model_id,
                "messages": messages,
                **{k: v for k, v in model_config.items() if v is not None}
            }
            
            response = requests.post(
                self.chat_endpoint,
                headers={"Content-Type": "application/json"},
                json=payload,
                timeout=self.config.timeout,
                stream=model_config.get("stream", False)
            )
            
            if response.status_code == 200:
                return True, response
            else:
                error_msg = f"HTTP {response.status_code} - {response.text}"
                print(f"❌ Chat request error: {error_msg}")
                return False, error_msg
                
        except Exception as e:
            error_msg = f"Request error: {e}"
            print(f"❌ Chat request error: {error_msg}")
            return False, error_msg
    
    def get_status_info(self, session_data: Dict, conversation_length: int = 0) -> Dict:
        """Get comprehensive status information for the API."""
        try:
            info = self.get_connection_info()
            loaded_models = self.get_loaded_models()
            
            # Test SSH connection
            ssh_status = False
            if self.config.ssh_user:
                print(f"🔍 Testing SSH connection to {self.config.ssh_user}@{self.config.host}...")
                ssh_status = self.check_ssh_connection()
            
            status_data = {
                "server": f"{self.config.host}:{self.config.port}",
                "http_connected": info['http_accessible'],
                "ssh_available": ssh_status,
                "ssh_configured": bool(self.config.ssh_user),
                "current_model": session_data.get('current_model'),
                "streaming": session_data.get('model_config', {}).get('stream', False),
                "conversation_length": conversation_length,
                "system_prompt": session_data.get('system_prompt', DEFAULT_SYSTEM_PROMPT),
                "session_id": session_data.get('session_id')[:8] if session_data.get('session_id') else None,
                "loaded_models": [{"id": m.id, "state": m.state, "quantization": m.quantization} for m in loaded_models],
                "available_models": [{"id": m.id, "state": m.state, "quantization": m.quantization} for m in info['models']]
            }
            
            return status_data
            
        except Exception as e:
            print(f"❌ Status check error: {e}")
            raise e

def setup_server_config() -> ServerConfig:
    """Interactive setup for server configuration."""
    print("🔧 LM Studio Server Configuration Setup")
    print("=" * 50)
    
    # Get server details
    host = input("Enter server IP address (e.g., localhost): ").strip()
    if not host:
        host = "localhost"
    
    port_input = input(f"Enter server port [1234]: ").strip()
    port = int(port_input) if port_input else 1234
    
    ssh_user = input("Enter SSH username (optional, press Enter to skip): ").strip()
    if not ssh_user:
        ssh_user = None
    
    config = ServerConfig(
        host=host,
        port=port,
        ssh_user=ssh_user
    )
    
    print(f"\n📋 Configuration Summary:")
    print(f"  Server: {host}:{port}")
    print(f"  SSH User: {ssh_user or 'Not configured'}")
    
    return config

def save_config(config: ServerConfig, filename: str = "lmstudio_config.json"):
    """Save configuration to file."""
    config_dict = {
        "host": config.host,
        "port": config.port,
        "ssh_user": config.ssh_user,
        "timeout": config.timeout
    }
    
    with open(filename, 'w') as f:
        json.dump(config_dict, f, indent=2)

def load_config(filename: str = "lmstudio_config.json") -> Optional[ServerConfig]:
    """Load configuration from file."""
    try:
        with open(filename, 'r') as f:
            config_dict = json.load(f)
        return ServerConfig(**config_dict)
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return None

# Example usage and testing functions
def main():
    """Example usage of LMStudioUtils."""
    print("LM Studio Utilities - Test")
    print("=" * 40)
    
    # Try to load existing config or create new one
    config = load_config()
    if not config:
        config = setup_server_config()
        save_config(config)
        print(f"\n💾 Configuration saved to lmstudio_config.json")
    
    # Initialize utils
    utils = LMStudioUtils(config)
    
    # Get connection info
    print(f"\n🔍 Checking HTTP connection to {config.host}:{config.port}...")
    info = utils.get_connection_info()
    
    print(f"HTTP accessible: {'✅' if info['http_accessible'] else '❌'}")
    print(f"Available models: {len(info['models'])}")
    print(f"Loaded models: {len(info['loaded_models'])}")
    
    if info['loaded_models']:
        print("\nLoaded models:")
        for model in info['loaded_models']:
            print(f"  • {model.id} ({model.quantization})")
    
    # Test ensuring server is ready
    print(f"\n🔍 Checking if server is ready...")
    success, msg = utils.ensure_server_ready()
    print(f"Result: {'✅' if success else '❌'} {msg}")

if __name__ == "__main__":
    main()