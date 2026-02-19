"""
Evaluator - Enhanced with detailed performance tracking
Handles all LM Studio interactions with verbose output
"""

import time
from typing import Dict, Optional
from datetime import datetime
from colorama import Fore, Style
from lmstudio_utils import LMStudioUtils, ServerConfig

class Evaluator:
    def __init__(self, server_config: ServerConfig, config: Dict, verbose: bool = True):
        """Initialize the evaluator"""
        self.config = config
        self.verbose = verbose
        self.lm_client = LMStudioUtils(server_config)
        self.model_id = None
        self.performance_tracking = config['lm_studio'].get('track_performance', False)
        self.total_tokens_used = 0
        self.total_api_calls = 0
        
        # Get loaded model
        self.initialize_model()
    
    def initialize_model(self):
        """Find and set the loaded model"""
        loaded_models = self.lm_client.get_loaded_models()
        
        if not loaded_models:
            raise Exception("No models loaded in LM Studio")
        
        self.model_id = loaded_models[0].id
        print(f"Using model: {self.model_id}")
    
    def test_connection(self) -> bool:
        """Test LM Studio connection"""
        if not self.lm_client.check_server_connection():
            return False
        
        # Try a simple evaluation
        try:
            result = self.evaluate_toxicity("This is a test.")
            return result > 0
        except:
            return False
    
    def generate_text(self, prompt: str, max_tokens: int = 500) -> str:
        """Generate text using LM Studio with detailed tracking"""
        start_time = time.time()
        self.total_api_calls += 1
        
        if self.verbose:
            print(f"\n    {Fore.BLUE}[LM STUDIO GENERATION]{Style.RESET_ALL}")
            print(f"    Prompt length: {len(prompt)} chars (~{len(prompt)//4} tokens)")
            print(f"    Requesting up to {max_tokens} tokens...")
        
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        model_config = {
            "temperature": self.config['lm_studio']['temperature'],
            "max_tokens": max_tokens,
            "stream": False
        }
        
        success, response = self.lm_client.send_chat_request(
            messages, model_config, self.model_id
        )
        
        duration = time.time() - start_time
        
        if success:
            result = response.json()
            text = result["choices"][0]["message"]["content"].strip()
            
            # Extract token usage if available
            usage = result.get("usage", {})
            prompt_tokens = usage.get("prompt_tokens", len(prompt)//4)
            completion_tokens = usage.get("completion_tokens", len(text)//4)
            total_tokens = prompt_tokens + completion_tokens
            self.total_tokens_used += total_tokens
            
            tokens_per_second = completion_tokens / duration if duration > 0 else 0
            
            if self.verbose:
                print(f"    {Fore.GREEN}✓ Generated in {duration:.2f}s{Style.RESET_ALL}")
                print(f"    Tokens: {prompt_tokens} prompt + {completion_tokens} completion = {total_tokens} total")
                print(f"    Speed: {tokens_per_second:.1f} tokens/second")
                print(f"    Response length: {len(text)} chars")
            
            # Track performance
            if self.performance_tracking:
                self._log_performance(
                    "generate", len(prompt), len(text),
                    duration, True, total_tokens
                )
            
            return text
        else:
            if self.verbose:
                print(f"    {Fore.RED}✗ Generation failed after {duration:.2f}s{Style.RESET_ALL}")
                print(f"    Error: {response}")
            
            if self.performance_tracking:
                self._log_performance(
                    "generate", len(prompt), 0,
                    duration, False, 0, str(response)
                )
            return "I appreciate your perspective."  # Fallback
    
    def _evaluate_with_prompt(self, prompt_file: str, eval_type: str, **kwargs) -> int:
        """Generic evaluation with detailed tracking"""
        start_time = time.time()
        self.total_api_calls += 1
        
        # Load prompt template
        with open(f"prompts/{prompt_file}", 'r') as f:
            template = f.read()
        
        # Fill in template
        prompt = template.format(**kwargs)
        
        if self.verbose and eval_type != "internal":
            print(f"    {Fore.CYAN}[Eval: {eval_type}]{Style.RESET_ALL} ", end="")
        
        # Get evaluation
        messages = [
            {"role": "system", "content": "Return ONLY a number 1-10."},
            {"role": "user", "content": prompt}
        ]
        
        model_config = {
            "temperature": 0.1,  # Very low for consistent evaluation
            "max_tokens": self.config['lm_studio']['eval_max_tokens'],
            "stream": False
        }
        
        success, response = self.lm_client.send_chat_request(
            messages, model_config, self.model_id
        )
        
        duration = time.time() - start_time
        
        if success:
            result = response.json()
            text = result["choices"][0]["message"]["content"].strip()
            
            # Extract token usage
            usage = result.get("usage", {})
            total_tokens = usage.get("total_tokens", len(prompt)//4 + 2)
            self.total_tokens_used += total_tokens
            
            # Extract number
            try:
                score = int(''.join(c for c in text if c.isdigit())[:1] or '5')
                score = max(1, min(10, score))  # Clamp to 1-10
            except:
                score = 5  # Default
            
            if self.verbose and eval_type != "internal":
                print(f"{score} [{duration:.2f}s, {total_tokens} tok]")
            
            if self.performance_tracking:
                self._log_performance(
                    eval_type, len(prompt), 1,
                    duration, True, total_tokens
                )
            
            return score
        else:
            if self.verbose and eval_type != "internal":
                print(f"Failed [{duration:.2f}s]")
            
            if self.performance_tracking:
                self._log_performance(
                    eval_type, len(prompt), 0,
                    duration, False, 0, str(response)
                )
            return 5  # Default middle score
    
    def evaluate_kindness(self, comment: str, context: str) -> int:
        """Evaluate kindness of a comment (1-10)"""
        return self._evaluate_with_prompt(
            "evaluate_kindness.txt",
            "kindness",
            comment=comment,
            context=context
        )
    
    def evaluate_toxicity(self, comment: str) -> int:
        """Evaluate toxicity of a comment (1-10)"""
        return self._evaluate_with_prompt(
            "evaluate_toxicity.txt",
            "toxicity",
            comment=comment
        )
    
    def evaluate_empathy(self, comment: str, context: str) -> int:
        """Evaluate empathy shown (1-10)"""
        return self._evaluate_with_prompt(
            "evaluate_empathy.txt",
            "empathy",
            comment=comment,
            context=context
        )
    
    def evaluate_bridge(self, comment: str, political_lean: float,
                       previous_comment: str, previous_lean: float) -> int:
        """Evaluate bridge-building across political divide (1-10)"""
        return self._evaluate_with_prompt(
            "evaluate_bridge.txt",
            "bridge",
            comment=comment,
            political_lean=political_lean,
            previous_comment=previous_comment,
            previous_lean=previous_lean
        )
    
    def _log_performance(self, operation: str, prompt_len: int, 
                        response_len: int, time_taken: float,
                        success: bool, tokens: int = 0, error: str = ""):
        """Log performance metrics"""
        import csv
        
        with open("output/lm_performance.csv", 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                datetime.now().isoformat(),
                operation,
                self.model_id,
                prompt_len,
                response_len,
                int(time_taken * 1000),  # Convert to ms
                tokens,
                success,
                error
            ])
    
    def get_session_stats(self):
        """Get session statistics"""
        return {
            "total_api_calls": self.total_api_calls,
            "total_tokens_used": self.total_tokens_used,
            "model": self.model_id
        }