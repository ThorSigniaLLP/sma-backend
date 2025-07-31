import logging
import uuid
import os
import base64
import requests
from typing import Optional, Dict, Any
from pathlib import Path
from app.config import get_settings
from app.services.cloudinary_service import cloudinary_service

logger = logging.getLogger(__name__)
settings = get_settings()


class ImageService:
    """Service for handling image storage and serving."""
    
    def __init__(self):
        # Create images directory if it doesn't exist
        self.images_dir = Path("temp_images")
        self.images_dir.mkdir(exist_ok=True)
        
        # Base URL for serving images (you may need to adjust this based on your setup)
        self.base_url = "http://localhost:8000/temp_images"
        
        # IMGBB configuration for public image hosting
        self.imgbb_api_key = settings.imgbb_api_key.strip() if settings.imgbb_api_key else None
        self.imgbb_endpoint = "https://api.imgbb.com/1/upload"
    
    def _upload_to_imgbb(self, base64_data: str) -> Optional[str]:
        """Upload a base64 image string to IMGBB and return the public URL."""
        if not self.imgbb_api_key:
            return None  # IMGBB not configured

        try:
            response = requests.post(
                self.imgbb_endpoint,
                params={"key": self.imgbb_api_key},
                data={"image": base64_data}
            )

            if response.status_code == 200:
                json_resp = response.json()
                if json_resp.get("success"):
                    # Use display_url which is direct image URL, better for Facebook
                    return json_resp["data"].get("display_url")
                logger.error(f"IMGBB upload returned success=false: {json_resp}")
            else:
                logger.error(f"IMGBB upload failed ({response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Exception during IMGBB upload: {e}")

        return None
    
    def save_base64_image(
        self,
        base64_data: str,
        filename: Optional[str] = None,
        format: str = "png"
    ) -> Dict[str, Any]:
        """
        Save a base64 encoded image to disk and return its URL.
        
        Args:
            base64_data: Base64 encoded image data
            filename: Optional filename (will generate UUID if not provided)
            format: Image format (png, jpg, etc.)
            
        Returns:
            Dict containing save result and image URL
        """
        try:
            # Generate filename if not provided
            if not filename:
                filename = f"{uuid.uuid4()}.{format}"
            elif not filename.endswith(f".{format}"):
                filename = f"{filename}.{format}"
            
            # Full path for the image
            file_path = self.images_dir / filename
            
            # Decode and save the image
            image_data = base64.b64decode(base64_data)
            
            with open(file_path, 'wb') as f:
                f.write(image_data)
            
            # Attempt to upload to IMGBB for a publicly accessible URL
            public_url = self._upload_to_imgbb(base64_data)

            # Fallback to local serving URL if IMGBB upload not configured or fails
            image_url = public_url if public_url else f"{self.base_url}/{filename}"
            
            logger.info(f"Image saved successfully: {filename}")
            
            return {
                "success": True,
                "filename": filename,
                "file_path": str(file_path),
                "image_url": image_url,
                "local_image_url": f"{self.base_url}/{filename}",
                "uploaded_to_imgbb": bool(public_url),
                "size": len(image_data)
            }
            
        except Exception as e:
            logger.error(f"Error saving image: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def delete_image(self, filename: str) -> Dict[str, Any]:
        """
        Delete an image file.
        
        Args:
            filename: Name of the file to delete
            
        Returns:
            Dict containing deletion result
        """
        try:
            file_path = self.images_dir / filename
            
            if file_path.exists():
                os.remove(file_path)
                logger.info(f"Image deleted successfully: {filename}")
                return {
                    "success": True,
                    "message": f"Image {filename} deleted successfully"
                }
            else:
                return {
                    "success": False,
                    "error": f"Image {filename} not found"
                }
                
        except Exception as e:
            logger.error(f"Error deleting image: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def cleanup_old_images(self, max_age_hours: int = 24) -> Dict[str, Any]:
        """
        Clean up old temporary images.
        
        Args:
            max_age_hours: Maximum age of images to keep (in hours)
            
        Returns:
            Dict containing cleanup result
        """
        try:
            import time
            
            current_time = time.time()
            max_age_seconds = max_age_hours * 3600
            deleted_count = 0
            
            for file_path in self.images_dir.iterdir():
                if file_path.is_file():
                    file_age = current_time - file_path.stat().st_mtime
                    if file_age > max_age_seconds:
                        os.remove(file_path)
                        deleted_count += 1
                        logger.info(f"Deleted old image: {file_path.name}")
            
            return {
                "success": True,
                "deleted_count": deleted_count,
                "message": f"Cleaned up {deleted_count} old images"
            }
            
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def get_image_info(self, filename: str) -> Dict[str, Any]:
        """
        Get information about an image file.
        
        Args:
            filename: Name of the image file
            
        Returns:
            Dict containing image information
        """
        try:
            file_path = self.images_dir / filename
            
            if not file_path.exists():
                return {
                    "success": False,
                    "error": f"Image {filename} not found"
                }
            
            stat = file_path.stat()
            
            return {
                "success": True,
                "filename": filename,
                "size": stat.st_size,
                "created": stat.st_ctime,
                "modified": stat.st_mtime,
                "url": f"{self.base_url}/{filename}"
            }
            
        except Exception as e:
            logger.error(f"Error getting image info: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def is_configured(self) -> bool:
        """Check if image service is properly configured."""
        return True  # Always configured since it uses local storage


# Create a singleton instance
image_service = ImageService() 

def ensure_cloudinary_url(image_url: str) -> str:
    """
    If image_url is a base64 data URL, upload it to Cloudinary and return the Cloudinary URL.
    Otherwise, return the original image_url.
    """
    if image_url and isinstance(image_url, str) and image_url.startswith("data:image/"):
        # Extract base64 data
        base64_data = image_url.split(",", 1)[1] if "," in image_url else image_url
        image_data = base64.b64decode(base64_data)
        upload_result = cloudinary_service.upload_image_with_instagram_transform(image_data)
        if upload_result["success"]:
            return upload_result["url"]
        else:
            raise Exception(f"Cloudinary upload failed: {upload_result['error']}")
    return image_url 