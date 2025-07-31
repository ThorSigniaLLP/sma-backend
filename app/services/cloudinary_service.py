import requests
import logging
from typing import Dict
from app.config import get_settings
import cloudinary
import cloudinary.uploader
import os

logger = logging.getLogger(__name__)
settings = get_settings()

class CloudinaryService:
    """Helper for authenticated uploads to Cloudinary with Instagram transforms."""

    def __init__(self):
        self.cloud_name = settings.cloudinary_cloud_name
        self.api_key = settings.cloudinary_api_key
        self.api_secret = settings.cloudinary_api_secret
        if not (self.cloud_name and self.api_key and self.api_secret):
            logger.warning("Cloudinary credentials not fully configured. Uploads will fail.")
        cloudinary.config(
            cloud_name=self.cloud_name,
            api_key=self.api_key,
            api_secret=self.api_secret
        )

    def is_configured(self) -> bool:
        return bool(self.cloud_name and self.api_key and self.api_secret)

    def upload_image_with_instagram_transform(self, image_data):
        """Upload an image to Cloudinary with Instagram-specific transforms."""
        if not self.is_configured():
            return {"success": False, "error": "Cloudinary not configured"}
        try:
            result = cloudinary.uploader.upload(
                image_data,
                transformation=[
                    {"width": 1080, "height": 1080, "crop": "fill", "gravity": "auto"}
                ],
                folder="instagram",
                format="jpg",
                quality="auto"
            )
            logger.info(f"Successfully uploaded image to Cloudinary: {result['secure_url']}")
            return {"success": True, "url": result["secure_url"]}
        except Exception as e:
            logger.error(f"Cloudinary image upload failed: {e}")
            return {"success": False, "error": str(e)}

    def upload_video_with_instagram_transform(self, file_or_base64) -> Dict:
        """Upload a video (file or base64) to Cloudinary with Instagram-specific transforms."""
        if not self.is_configured():
            return {"success": False, "error": "Cloudinary not configured"}
        try:
            result = cloudinary.uploader.upload(
                file_or_base64,
                resource_type="video",
                transformation=[
                    {"width": 1080, "height": 1920, "crop": "fill"},
                    {"start_offset": "0", "end_offset": "90"},  # Trim to 90s
                    {"quality": "auto"},
                    {"fetch_format": "mp4"}
                ],
                format="mp4"
            )
            return {"success": True, "url": result["secure_url"]}
        except Exception as e:
            logger.error(f"Cloudinary video upload failed: {e}")
            return {"success": False, "error": str(e)}

    def upload_thumbnail_with_instagram_transform(self, image_data) -> Dict:
        """Upload a thumbnail image to Cloudinary with Instagram reel cover image requirements."""
        if not self.is_configured():
            return {"success": False, "error": "Cloudinary not configured"}
        try:
            # Instagram reel cover image requirements:
            # - Aspect ratio: 9:16 (vertical)
            # - Minimum resolution: 420x654 pixels
            # - Recommended resolution: 1080x1920 pixels
            # - Format: JPG or PNG
            result = cloudinary.uploader.upload(
                image_data,
                transformation=[
                    {"width": 1080, "height": 1920, "crop": "fill", "gravity": "auto"},
                    {"quality": "auto:good"},
                    {"format": "jpg"}
                ],
                folder="instagram/thumbnails",
                format="jpg",
                quality="auto:good"
            )
            logger.info(f"Successfully uploaded thumbnail to Cloudinary: {result['secure_url']}")
            return {"success": True, "url": result["secure_url"]}
        except Exception as e:
            logger.error(f"Cloudinary thumbnail upload failed: {e}")
            return {"success": False, "error": str(e)}

cloudinary_service = CloudinaryService() 