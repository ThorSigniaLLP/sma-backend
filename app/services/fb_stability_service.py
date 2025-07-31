import logging
import httpx
import base64
import io
from typing import Optional, Dict, Any
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class StabilityService:
    """Service for Stability AI image generation operations."""
    
    def __init__(self):
        self.api_base = "https://api.stability.ai"
        self.api_key = settings.stability_api_key.strip() if settings.stability_api_key else None
        self.engine_id = "stable-diffusion-v1-6"  # Default engine
    
    async def generate_image(
        self,
        prompt: str,
        negative_prompt: Optional[str] = None,
        width: int = 1024,
        height: int = 1024,
        cfg_scale: float = 7.0,
        steps: int = 30,
        samples: int = 1,
        style_preset: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate an image using Stability AI.
        
        Args:
            prompt: Text description of the image to generate
            negative_prompt: What to avoid in the image
            width: Image width (64-2048, must be multiple of 64)
            height: Image height (64-2048, must be multiple of 64)
            cfg_scale: How strictly the diffusion process adheres to the prompt (0-35)
            steps: Number of diffusion steps (10-150)
            samples: Number of images to generate (1-10)
            style_preset: Style preset to apply
            
        Returns:
            Dict containing generation result and image data
        """
        if not self.api_key:
            return {
                "success": False,
                "error": "Stability AI API key not configured"
            }
        
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json"
            }
            
            # Prepare request data
            data = {
                "text_prompts": [
                    {
                        "text": prompt,
                        "weight": 1.0
                    }
                ],
                "cfg_scale": cfg_scale,
                "width": width,
                "height": height,
                "steps": steps,
                "samples": samples
            }
            
            # Add negative prompt if provided
            if negative_prompt:
                data["text_prompts"].append({
                    "text": negative_prompt,
                    "weight": -1.0
                })
            
            # Add style preset if provided
            if style_preset:
                data["style_preset"] = style_preset
            
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{self.api_base}/v1/generation/{self.engine_id}/text-to-image",
                    headers=headers,
                    json=data
                )
                
                if response.status_code == 200:
                    result = response.json()
                    artifacts = result.get("artifacts", [])
                    
                    if artifacts:
                        # Get the first generated image
                        image_data = artifacts[0]
                        image_base64 = image_data.get("base64")
                        
                        return {
                            "success": True,
                            "image_base64": image_base64,
                            "seed": image_data.get("seed"),
                            "finish_reason": image_data.get("finishReason"),
                            "prompt": prompt,
                            "width": width,
                            "height": height,
                            "cfg_scale": cfg_scale,
                            "steps": steps
                        }
                    else:
                        return {
                            "success": False,
                            "error": "No images generated"
                        }
                else:
                    error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"message": response.text}
                    logger.error(f"Stability AI API error: {error_data}")
                    return {
                        "success": False,
                        "error": f"API error: {error_data.get('message', 'Unknown error')}"
                    }
                    
        except Exception as e:
            logger.error(f"Error generating image with Stability AI: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def generate_image_with_facebook_optimization(
        self,
        prompt: str,
        post_type: str = "feed"
    ) -> Dict[str, Any]:
        """
        Generate an image optimized for Facebook posts.
        
        Args:
            prompt: Text description of the image
            post_type: Type of Facebook post (feed, story, cover)
            
        Returns:
            Dict containing generation result
        """
        # Facebook-optimized dimensions (must be multiples of 64)
        dimensions = {
            "feed": (1216, 640),      # Standard Facebook post (close to 1200x630)
            "story": (1088, 1920),    # Facebook Story (close to 1080x1920)
            "cover": (1664, 832),     # Facebook Cover Photo (close to 1640x859)
            "profile": (384, 384),    # Profile picture (close to 400x400)
            "square": (1088, 1088)    # Square post (close to 1080x1080)
        }
        
        width, height = dimensions.get(post_type, dimensions["feed"])
        
        # Enhance prompt for social media
        enhanced_prompt = f"High-quality, engaging, professional social media image: {prompt}, vibrant colors, good lighting, visually appealing"
        
        # Add negative prompt for better quality
        negative_prompt = "blurry, low quality, distorted, text overlay, watermark, ugly, bad anatomy"
        
        return await self.generate_image(
            prompt=enhanced_prompt,
            negative_prompt=negative_prompt,
            width=width,
            height=height,
            cfg_scale=8.0,  # Slightly higher for better prompt adherence
            steps=40,       # More steps for better quality
            samples=1
        )
    
    def convert_base64_to_bytes(self, base64_string: str) -> bytes:
        """
        Convert base64 string to bytes.
        
        Args:
            base64_string: Base64 encoded image data
            
        Returns:
            Image bytes
        """
        return base64.b64decode(base64_string)
    
    def is_configured(self) -> bool:
        """Check if Stability AI service is properly configured."""
        return bool(self.api_key)


# Create a singleton instance
stability_service = StabilityService() 