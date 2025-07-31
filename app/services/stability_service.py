import requests
import logging
from typing import Dict, Optional
from app.config import get_settings
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)
settings = get_settings()

class StabilityService:
    """Service for Stability AI image generation."""
    
    def __init__(self):
        self.api_key = os.getenv('STABILITY_API_KEY')
        self.api_host = "https://api.stability.ai"
        self.engine_id = "stable-diffusion-xl-1024-v1-0"
        
    async def generate_image(
        self, 
        prompt: str, 
        negative_prompt: str = None,
        width: int = 1024,
        height: int = 1024,
        cfg_scale: float = 7.0,
        steps: int = 30,
        samples: int = 1
    ) -> Dict:
        """Generate an image using Stability AI."""
        try:
            # Check if API key is configured
            if not self.api_key:
                logger.error("Stability AI API key not configured")
                return {
                    "success": False,
                    "error": "Stability AI API key not configured. Please set STABILITY_API_KEY environment variable."
                }
            
            # Log API key status (without exposing the full key)
            api_key_status = "Configured" if self.api_key else "Not configured"
            logger.info(f"Stability AI API key status: {api_key_status}")
            if self.api_key:
                logger.info(f"API key starts with: {self.api_key[:10]}...")
            
            url = f"{self.api_host}/v1/generation/{self.engine_id}/text-to-image"
            
            headers = {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            # Prepare text prompts
            text_prompts = [{"text": prompt, "weight": 1.0}]
            
            # Add negative prompt if provided
            if negative_prompt:
                text_prompts.append({"text": negative_prompt, "weight": -1.0})
            
            payload = {
                "text_prompts": text_prompts,
                "cfg_scale": cfg_scale,
                "height": height,
                "width": width,
                "samples": samples,
                "steps": steps,
            }
            
            logger.info(f"Making request to Stability AI with prompt: {prompt[:50]}...")
            logger.info(f"Dimensions: {width}x{height}, Steps: {steps}, CFG: {cfg_scale}")
            response = requests.post(url, headers=headers, json=payload)
            
            # Handle different HTTP status codes
            if response.status_code == 401:
                logger.error("Stability AI API key is invalid or expired")
                return {
                    "success": False,
                    "error": "Invalid or expired Stability AI API key. Please check your API key in the .env file."
                }
            elif response.status_code == 429:
                logger.error("Stability AI rate limit exceeded")
                return {
                    "success": False,
                    "error": "Rate limit exceeded. Please wait a few minutes before trying again."
                }
            elif response.status_code != 200:
                logger.error(f"Stability AI API error: {response.status_code} - {response.text}")
                return {
                    "success": False,
                    "error": f"Stability AI API error: {response.status_code} - {response.text}"
                }
            
            response.raise_for_status()
            
            # Extract base64 image from response
            result = response.json()
            if "artifacts" in result and len(result["artifacts"]) > 0:
                logger.info("Image generated successfully")
                return {
                    "success": True,
                    "image_base64": result["artifacts"][0]["base64"],
                    "seed": result["artifacts"][0].get("seed"),
                    "finish_reason": result["artifacts"][0].get("finishReason")
                }
            
            return {
                "success": False,
                "error": "No image generated"
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Stability AI request failed: {e}")
            return {
                "success": False,
                "error": f"Request failed: {str(e)}"
            }
        except Exception as e:
            logger.error(f"Stability AI image generation failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def is_configured(self) -> bool:
        """Check if Stability service is properly configured."""
        return bool(self.api_key)

# Global service instance
stability_service = StabilityService()
