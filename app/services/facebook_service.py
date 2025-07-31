import logging
import httpx
import os
import aiohttp
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from app.config import get_settings
from app.services.groq_service import groq_service
from app.services.fb_stability_service import stability_service
from app.services.image_service import image_service

logger = logging.getLogger(__name__)
settings = get_settings()


class FacebookService:
    """Service for Facebook API operations and integrations."""
    
    def __init__(self):
        self.graph_api_base = "https://graph.facebook.com/v23.0"
        self.app_id = settings.facebook_app_id
        self.app_secret = settings.facebook_app_secret
    
    async def exchange_for_long_lived_token(self, short_lived_token: str) -> Dict[str, Any]:
        """
        Exchange a short-lived access token for a long-lived token.
        
        Args:
            short_lived_token: Short-lived Facebook access token
            
        Returns:
            Dict containing the long-lived token and expiration info
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.graph_api_base}/oauth/access_token",
                    params={
                        "grant_type": "fb_exchange_token",
                        "client_id": self.app_id,
                        "client_secret": self.app_secret,
                        "fb_exchange_token": short_lived_token
                    }
                )
                
                if response.status_code == 200:
                    token_data = response.json()
                    
                    # Calculate expiration time (default to 60 days if not specified)
                    expires_in_seconds = token_data.get("expires_in", 5184000)  # 60 days default
                    expires_at = datetime.utcnow() + timedelta(seconds=expires_in_seconds)
                    
                    return {
                        "success": True,
                        "access_token": token_data.get("access_token"),
                        "token_type": token_data.get("token_type", "bearer"),
                        "expires_in": expires_in_seconds,
                        "expires_at": expires_at
                    }
                else:
                    logger.error(f"Token exchange failed: {response.text}")
                    return {
                        "success": False,
                        "error": f"Token exchange failed: {response.text}"
                    }
                    
        except Exception as e:
            logger.error(f"Error exchanging token: {e}")
            return {"success": False, "error": str(e)}

    async def validate_and_refresh_token(self, access_token: str, expires_at: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Validate an access token and refresh if needed.
        
        Args:
            access_token: Facebook access token to validate
            expires_at: Known expiration time of the token
            
        Returns:
            Dict containing validation result and potentially new token
        """
        try:
            # Check if token is expired based on stored expiration time
            if expires_at and expires_at <= datetime.utcnow():
                logger.info("Token is expired based on stored expiration time")
                return {
                    "valid": False,
                    "expired": True,
                    "error": "Token has expired",
                    "needs_reconnection": True
                }
            
            # Validate token with Facebook API
            validation_result = await self.validate_access_token(access_token)
            
            if not validation_result["valid"]:
                # Check if it's an expiration error
                error_msg = validation_result.get("error", "")
                if "expired" in error_msg.lower() or "session" in error_msg.lower():
                    return {
                        "valid": False,
                        "expired": True,
                        "error": error_msg,
                        "needs_reconnection": True
                    }
                else:
                    return validation_result
            
            # Token is valid, check if it's close to expiration and needs refresh
            # Note: For long-lived tokens, Facebook auto-refreshes them if the user is active
            return {
                "valid": True,
                "user_id": validation_result.get("user_id"),
                "name": validation_result.get("name"),
                "email": validation_result.get("email"),
                "picture": validation_result.get("picture")
            }
            
        except Exception as e:
            logger.error(f"Error validating/refreshing token: {e}")
            return {"valid": False, "error": str(e)}

    async def get_long_lived_page_tokens(self, long_lived_user_token: str) -> List[Dict[str, Any]]:
        """
        Get long-lived page access tokens from a long-lived user token.
        
        Args:
            long_lived_user_token: Long-lived user access token
            
        Returns:
            List of pages with long-lived page access tokens
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.graph_api_base}/me/accounts",
                    params={
                        "access_token": long_lived_user_token,
                        "fields": "id,name,category,access_token,picture,fan_count,tasks"
                    }
                )
                
                if response.status_code == 200:
                    pages_data = response.json()
                    pages = pages_data.get("data", [])
                    
                    # Page access tokens from long-lived user tokens are automatically long-lived
                    # and don't expire unless the user changes password, revokes permissions, etc.
                    for page in pages:
                        page["token_type"] = "long_lived_page_token"
                        page["expires_at"] = None  # Page tokens don't have explicit expiration
                    
                    return pages
                else:
                    logger.error(f"Failed to get page tokens: {response.text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting page tokens: {e}")
            return []

    async def validate_access_token(self, access_token: str) -> Dict[str, Any]:
        """
        Validate Facebook access token (works for both user and page tokens).
        
        Args:
            access_token: Facebook access token
            
        Returns:
            Dict containing validation result and user/page info
        """
        try:
            async with httpx.AsyncClient() as client:
                # First try to get basic info without email (works for both users and pages)
                response = await client.get(
                    f"{self.graph_api_base}/me",
                    params={
                        "access_token": access_token,
                        "fields": "id,name,picture"
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # Try to determine if this is a user or page token
                    # Pages have different structure and no email
                    result = {
                        "valid": True,
                        "user_id": data.get("id"),
                        "name": data.get("name"),
                        "picture": data.get("picture", {}).get("data", {}).get("url") if isinstance(data.get("picture"), dict) else data.get("picture")
                    }
                    
                    # Try to get email if it's a user token (will fail silently for page tokens)
                    try:
                        email_response = await client.get(
                            f"{self.graph_api_base}/me",
                            params={
                                "access_token": access_token,
                                "fields": "email"
                            }
                        )
                        if email_response.status_code == 200:
                            email_data = email_response.json()
                            result["email"] = email_data.get("email")
                    except:
                        # Email field not available (probably a page token)
                        result["email"] = None
                    
                    return result
                else:
                    error_data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {"error": {"message": response.text}}
                    error_message = error_data.get("error", {}).get("message", "Invalid access token")
                    logger.error(f"Token validation failed: {error_message}")
                    return {"valid": False, "error": error_message}
                    
        except Exception as e:
            logger.error(f"Error validating Facebook token: {e}")
            return {"valid": False, "error": str(e)}
    
    async def get_user_pages(self, access_token: str) -> List[Dict[str, Any]]:
        """
        Get user's Facebook pages.
        
        Args:
            access_token: Facebook access token
            
        Returns:
            List of user's Facebook pages
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.graph_api_base}/me/accounts",
                    params={
                        "access_token": access_token,
                        "fields": "id,name,category,access_token,picture,fan_count"
                    }
                )
                
                if response.status_code == 200:
                    pages_data = response.json()
                    return pages_data.get("data", [])
                else:
                    logger.error(f"Failed to get pages: {response.text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting Facebook pages: {e}")
            return []
    
    async def create_post(
        self, 
        page_id: str, 
        access_token: str, 
        message: str,
        link: Optional[str] = None,
        media_url: Optional[str] = None,
        media_type: str = "text",
        media_file_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create a Facebook post.
        
        Args:
            page_id: Facebook page ID
            access_token: Page access token
            message: Post message content
            link: Optional link to include
            media_url: Optional media URL (for external URLs)
            media_type: Type of media (text, photo, video)
            media_file_path: Optional local file path for direct upload
            
        Returns:
            Dict containing post creation result
        """
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                endpoint = f"{self.graph_api_base}/{page_id}/feed"
                
                data = {
                    "message": message,
                    "access_token": access_token
                }
                
                # Add link if provided
                if link:
                    data["link"] = link
                
                # Handle media posts
                response = None  # Initialize response variable
                if media_type == "photo":
                    endpoint = f"{self.graph_api_base}/{page_id}/photos"
                    data["caption"] = message  # Use caption for photos
                    del data["message"]  # Remove message for photo posts
                    
                    if media_url:
                        # Check if media_url is a base64 data URL
                        if media_url.startswith('data:image/'):
                            # Handle base64 data URL
                            logger.info(f"Detected base64 image data, converting for upload")
                            try:
                                # Extract the base64 data and format
                                header, base64_data = media_url.split(',', 1)
                                import base64
                                import tempfile
                                
                                # Decode base64 data
                                image_data = base64.b64decode(base64_data)
                                
                                # Determine file extension from data URL
                                if 'image/jpeg' in header or 'image/jpg' in header:
                                    ext = 'jpg'
                                    content_type = 'image/jpeg'
                                elif 'image/png' in header:
                                    ext = 'png'
                                    content_type = 'image/png'
                                elif 'image/gif' in header:
                                    ext = 'gif'
                                    content_type = 'image/gif'
                                else:
                                    ext = 'jpg'  # Default fallback
                                    content_type = 'image/jpeg'
                                
                                # Upload directly using httpx files parameter
                                files = {
                                    "source": (f"image.{ext}", image_data, content_type)
                                }
                                
                                logger.info(f"Uploading base64 image to Facebook: {len(image_data)} bytes")
                                logger.info(f"Sending to endpoint: {endpoint}")
                                logger.info(f"Data: {data}")
                                
                                response = await client.post(endpoint, data=data, files=files)
                                logger.info(f"Facebook response status: {response.status_code}")
                                logger.info(f"Facebook response text: {response.text}")
                                
                            except Exception as base64_error:
                                logger.error(f"Error processing base64 image: {base64_error}")
                                return {
                                    "success": False,
                                    "error": f"Failed to process uploaded image: {str(base64_error)}"
                                }
                        else:
                            # Use URL for IMGBB hosted images or other URLs
                            logger.info(f"Using image URL: {media_url}")
                            logger.info(f"Facebook photos endpoint: {endpoint}")
                            logger.info(f"Data being sent: {data}")
                            data["url"] = media_url
                            response = await client.post(endpoint, data=data)
                            logger.info(f"Facebook response status: {response.status_code}")
                            logger.info(f"Facebook response text: {response.text}")
                    elif media_file_path and os.path.exists(media_file_path):
                        # Upload file directly
                        logger.info(f"Uploading image file directly: {media_file_path}")
                        logger.info(f"File size: {os.path.getsize(media_file_path)} bytes")
                        
                        try:
                            # Prepare multipart form data
                            with open(media_file_path, "rb") as f:
                                files = {
                                    "source": ("image.png", f, "image/png")
                                }
                                
                                # Log the data being sent
                                logger.info(f"Sending data to Facebook: {data}")
                                logger.info(f"Endpoint: {endpoint}")
                                
                                # Use httpx for multipart upload
                                response = await client.post(
                                    endpoint,
                                    data=data,
                                    files=files
                                )
                                
                                logger.info(f"Facebook API response status: {response.status_code}")
                                logger.info(f"Facebook API response headers: {dict(response.headers)}")
                                
                        except Exception as file_error:
                            logger.error(f"Error reading image file {media_file_path}: {file_error}")
                            return {
                                "success": False,
                                "error": f"Failed to read image file: {str(file_error)}"
                            }
                        
                    else:
                        return {
                            "success": False,
                            "error": "No image file or URL provided for photo post"
                        }
                        
                elif media_type == "video":
                    endpoint = f"{self.graph_api_base}/{page_id}/videos"
                    data["description"] = message
                    del data["message"]
                    
                    if media_url:
                        # Check if media_url is a base64 data URL
                        if media_url.startswith('data:video/'):
                            # Handle base64 video data URL
                            logger.info(f"Detected base64 video data, converting for upload")
                            try:
                                # Extract the base64 data and format
                                header, base64_data = media_url.split(',', 1)
                                import base64
                                
                                # Decode base64 data
                                video_data = base64.b64decode(base64_data)
                                
                                # Determine file extension from data URL
                                if 'video/mp4' in header:
                                    ext = 'mp4'
                                    content_type = 'video/mp4'
                                elif 'video/avi' in header:
                                    ext = 'avi'
                                    content_type = 'video/avi'
                                elif 'video/mov' in header:
                                    ext = 'mov'
                                    content_type = 'video/quicktime'
                                elif 'video/webm' in header:
                                    ext = 'webm'
                                    content_type = 'video/webm'
                                else:
                                    ext = 'mp4'  # Default fallback
                                    content_type = 'video/mp4'
                                
                                # Upload directly using httpx files parameter
                                files = {
                                    "source": (f"video.{ext}", video_data, content_type)
                                }
                                
                                logger.info(f"Uploading base64 video to Facebook: {len(video_data)} bytes")
                                logger.info(f"Sending to endpoint: {endpoint}")
                                logger.info(f"Data: {data}")
                                
                                response = await client.post(endpoint, data=data, files=files)
                                logger.info(f"Facebook response status: {response.status_code}")
                                logger.info(f"Facebook response text: {response.text}")
                                
                            except Exception as base64_error:
                                logger.error(f"Error processing base64 video: {base64_error}")
                                return {
                                    "success": False,
                                    "error": f"Failed to process uploaded video: {str(base64_error)}"
                                }
                        else:
                            # Use URL for hosted videos
                            logger.info(f"Using video URL: {media_url}")
                            data["file_url"] = media_url
                            response = await client.post(endpoint, data=data)
                            logger.info(f"Facebook response status: {response.status_code}")
                            logger.info(f"Facebook response text: {response.text}")
                    elif media_file_path and os.path.exists(media_file_path):
                        # Upload file directly
                        logger.info(f"Uploading video file directly: {media_file_path}")
                        logger.info(f"File size: {os.path.getsize(media_file_path)} bytes")
                        
                        try:
                            # Determine content type based on file extension
                            file_ext = os.path.splitext(media_file_path)[1].lower()
                            if file_ext == '.mp4':
                                content_type = 'video/mp4'
                            elif file_ext == '.avi':
                                content_type = 'video/avi'
                            elif file_ext == '.mov':
                                content_type = 'video/quicktime'
                            elif file_ext == '.webm':
                                content_type = 'video/webm'
                            else:
                                content_type = 'video/mp4'  # Default
                            
                            # Prepare multipart form data
                            with open(media_file_path, "rb") as f:
                                files = {
                                    "source": (os.path.basename(media_file_path), f, content_type)
                                }
                                
                                # Log the data being sent
                                logger.info(f"Sending data to Facebook: {data}")
                                logger.info(f"Endpoint: {endpoint}")
                                
                                # Use httpx for multipart upload
                                response = await client.post(
                                    endpoint,
                                    data=data,
                                    files=files
                                )
                                
                                logger.info(f"Facebook API response status: {response.status_code}")
                                logger.info(f"Facebook API response headers: {dict(response.headers)}")
                                
                        except Exception as file_error:
                            logger.error(f"Error reading video file {media_file_path}: {file_error}")
                            return {
                                "success": False,
                                "error": f"Failed to read video file: {str(file_error)}"
                            }
                    else:
                        return {
                            "success": False,
                            "error": "No video file or URL provided for video post"
                        }
                else:
                    # Text-only post
                    response = await client.post(endpoint, data=data)
                
                # Check if response was set
                if response is None:
                    return {
                        "success": False,
                        "error": "No response received from Facebook API"
                    }
                
                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Facebook post created successfully: {result}")
                    return {
                        "success": True,
                        "post_id": result.get("id"),
                        "message": "Post created successfully"
                    }
                else:
                    # Enhanced error handling for Facebook API responses
                    logger.error(f"Facebook API error - Status: {response.status_code}")
                    logger.error(f"Facebook API error - Response text: {response.text}")
                    logger.error(f"Facebook API error - Response headers: {dict(response.headers)}")
                    
                    try:
                        error_data = response.json()
                        logger.error(f"Facebook API error - Parsed JSON: {error_data}")
                        
                        # Extract detailed error information
                        if "error" in error_data:
                            fb_error = error_data["error"]
                            error_message = fb_error.get("message", "Unknown Facebook API error")
                            error_code = fb_error.get("code", "Unknown")
                            error_type = fb_error.get("type", "Unknown")
                            
                            full_error = f"Facebook API Error (Code: {error_code}, Type: {error_type}): {error_message}"
                            logger.error(f"Detailed Facebook error: {full_error}")
                            
                            return {
                                "success": False,
                                "error": full_error
                            }
                        else:
                            return {
                                "success": False,
                                "error": f"Facebook API error: {error_data}"
                            }
                    except Exception as json_error:
                        logger.error(f"Failed to parse Facebook error response as JSON: {json_error}")
                        logger.error(f"Raw response content type: {response.headers.get('content-type')}")
                        return {
                            "success": False,
                            "error": f"HTTP {response.status_code}: {response.text}"
                        }
                    
        except Exception as e:
            logger.error(f"Error creating Facebook post: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def create_ai_generated_post(
        self,
        page_id: str,
        access_token: str,
        prompt: str,
        media_url: Optional[str] = None,
        media_type: str = "text"
    ) -> Dict[str, Any]:
        """
        Create a Facebook post with AI-generated content.
        
        Args:
            page_id: Facebook page ID
            access_token: Page access token
            prompt: User prompt for AI generation
            media_url: Optional media URL
            media_type: Type of media
            
        Returns:
            Dict containing post creation result
        """
        try:
            # Generate content using Groq AI
            ai_result = await groq_service.generate_facebook_post(prompt)
            
            if not ai_result["success"]:
                return {
                    "success": False,
                    "error": f"AI generation failed: {ai_result.get('error', 'Unknown error')}"
                }
            
            generated_content = ai_result["content"]
            
            # Create the post with generated content
            post_result = await self.create_post(
                page_id=page_id,
                access_token=access_token,
                message=generated_content,
                media_url=media_url,
                media_type=media_type
            )
            
            # Add AI metadata to response
            if post_result["success"]:
                post_result.update({
                    "ai_generated": True,
                    "original_prompt": prompt,
                    "model_used": ai_result["model_used"],
                    "tokens_used": ai_result["tokens_used"]
                })
            
            return post_result
            
        except Exception as e:
            logger.error(f"Error creating AI-generated post: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def setup_auto_reply(
        self,
        page_id: str,
        access_token: str,
        enabled: bool,
        template: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Setup auto-reply for Facebook page comments.
        
        Args:
            page_id: Facebook page ID
            access_token: Page access token
            enabled: Whether to enable auto-reply
            template: Optional custom response template
            
        Returns:
            Dict containing setup result
        """
        try:
            # In a real implementation, you would:
            # 1. Set up Facebook webhooks for comment events
            # 2. Store auto-reply settings in database
            # 3. Configure webhook endpoint to handle comment events
            
            # For now, we'll just store the setting (you'll need to implement webhook handling)
            logger.info(f"Auto-reply {'enabled' if enabled else 'disabled'} for page {page_id}")
            
            return {
                "success": True,
                "message": f"Auto-reply {'enabled' if enabled else 'disabled'} successfully",
                "page_id": page_id,
                "enabled": enabled,
                "template": template or "Thank you for your comment! We appreciate your engagement."
            }
            
        except Exception as e:
            logger.error(f"Error setting up auto-reply: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def handle_comment_auto_reply(
        self,
        comment_id: str,
        comment_text: str,
        page_access_token: str,
        context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Handle automatic reply to a Facebook comment.
        
        Args:
            comment_id: Facebook comment ID
            comment_text: Content of the comment
            page_access_token: Page access token
            context: Additional context for the reply
            
        Returns:
            Dict containing reply result
        """
        try:
            # Generate AI reply
            reply_result = await groq_service.generate_auto_reply(comment_text, context)
            
            if not reply_result["success"]:
                # Use fallback reply
                reply_content = "Thank you for your comment! We appreciate your engagement. 😊"
            else:
                reply_content = reply_result["content"]
            
            # Post reply to Facebook
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.graph_api_base}/{comment_id}/comments",
                    data={
                        "message": reply_content,
                        "access_token": page_access_token
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        "success": True,
                        "reply_id": result.get("id"),
                        "reply_content": reply_content,
                        "ai_generated": reply_result["success"]
                    }
                else:
                    error_data = response.json()
                    logger.error(f"Failed to post reply: {error_data}")
                    return {
                        "success": False,
                        "error": error_data.get("error", {}).get("message", "Unknown error")
                    }
                    
        except Exception as e:
            logger.error(f"Error handling auto-reply: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def generate_image_only(
        self,
        image_prompt: str,
        post_type: str = "feed"
    ) -> Dict[str, Any]:
        """
        Generate an image without posting to Facebook.
        
        Args:
            image_prompt: Prompt for image generation
            post_type: Type of post for sizing (feed, story, etc.)
            
        Returns:
            Dict containing generation result and image URL
        """
        try:
            # Check if Stability AI is configured
            if not stability_service.is_configured():
                return {
                    "success": False,
                    "error": "Stability AI service not configured. Please set STABILITY_API_KEY."
                }
            
            # Generate image with Stability AI
            logger.info(f"Generating image for prompt: {image_prompt}")
            image_result = await stability_service.generate_image_with_facebook_optimization(
                prompt=image_prompt,
                post_type=post_type
            )
            
            if not image_result["success"]:
                return {
                    "success": False,
                    "error": f"Image generation failed: {image_result.get('error', 'Unknown error')}"
                }
            
            # Save the generated image
            save_result = image_service.save_base64_image(
                base64_data=image_result["image_base64"],
                format="png"
            )
            
            if not save_result["success"]:
                return {
                    "success": False,
                    "error": f"Failed to save generated image: {save_result.get('error', 'Unknown error')}"
                }
            
            return {
                "success": True,
                "image_url": save_result["image_url"],
                "filename": save_result["filename"],
                "prompt": image_prompt,
                "image_details": {
                    "width": image_result["width"],
                    "height": image_result["height"],
                    "seed": image_result.get("seed"),
                    "finish_reason": image_result.get("finish_reason")
                }
            }
            
        except Exception as e:
            logger.error(f"Error generating image: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def generate_and_post_image(
        self,
        page_id: str,
        access_token: str,
        image_prompt: str,
        text_content: str,
        post_type: str = "feed"
    ) -> Dict[str, Any]:
        """
        Generate an image and post it to Facebook with enhanced debugging.
        
        Args:
            page_id: Facebook page ID
            access_token: Page access token
            image_prompt: Prompt for image generation
            text_content: Text content for the post
            post_type: Type of post for sizing
            
        Returns:
            Dict containing the result
        """
        try:
            logger.info(f"=== GENERATE AND POST IMAGE DEBUG ===")
            logger.info(f"Page ID: {page_id}")
            logger.info(f"Access token length: {len(access_token) if access_token else 0}")
            logger.info(f"Image prompt: {image_prompt}")
            logger.info(f"Text content: {text_content}")
            
            # Step 1: Generate image
            logger.info("Step 1: Generating image with Stability AI")
            image_result = await stability_service.generate_image_with_facebook_optimization(
                prompt=image_prompt,
                post_type=post_type
            )
            
            if not image_result["success"]:
                logger.error(f"Image generation failed: {image_result.get('error')}")
                return {
                    "success": False,
                    "error": f"Image generation failed: {image_result.get('error', 'Unknown error')}"
                }
            
            logger.info(f"Image generated successfully: {image_result.get('width')}x{image_result.get('height')}")
            
            # Step 2: Save image
            logger.info("Step 2: Saving generated image")
            save_result = image_service.save_base64_image(
                base64_data=image_result["image_base64"],
                format="png"
            )
            
            if not save_result["success"]:
                logger.error(f"Image save failed: {save_result.get('error')}")
                return {
                    "success": False,
                    "error": f"Failed to save image: {save_result.get('error', 'Unknown error')}"
                }
            
            image_url = save_result["image_url"]
            logger.info(f"Image saved successfully: {image_url}")
            
            # Step 3: Post to Facebook
            logger.info("Step 3: Posting to Facebook")
            logger.info(f"Image URL: {image_url}")
            logger.info(f"Text content: {text_content}")
            
            post_result = await self.create_post(
                page_id=page_id,
                access_token=access_token,
                message=text_content,
                media_url=image_url,
                media_type="photo"
            )
            
            logger.info(f"Facebook post result: {post_result}")
            
            if post_result["success"]:
                logger.info("=== GENERATE AND POST SUCCESS ===")
                return {
                    "success": True,
                    "post_id": post_result["post_id"],
                    "image_url": image_url,
                    "text_content": text_content,
                    "image_details": {
                        "width": image_result.get("width"),
                        "height": image_result.get("height"),
                        "seed": image_result.get("seed")
                    }
                }
            else:
                logger.error("=== GENERATE AND POST FAILED ===")
                logger.error(f"Facebook post failed: {post_result.get('error')}")
                return post_result
                
        except Exception as e:
            logger.error(f"=== GENERATE AND POST ERROR ===")
            logger.error(f"Unexpected error: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
            return {
                "success": False,
                "error": str(e)
            }
    
    def is_configured(self) -> bool:
        """Check if Facebook service is properly configured."""
        return bool(self.app_id and self.app_secret)

    async def poll_and_auto_reply(self, page_id: str, access_token: str, last_checked: Optional[datetime] = None):
        """
        Polls Facebook for new comments and auto-replies to them if not already replied.
        Args:
            page_id: Facebook page ID
            access_token: Page access token
            last_checked: Only check comments after this time (for efficiency)
        """
        since_param = int(last_checked.timestamp()) if last_checked else int((datetime.utcnow() - timedelta(minutes=10)).timestamp())

        # 1. Get recent posts
        async with httpx.AsyncClient() as client:
            posts_resp = await client.get(
                f"{self.graph_api_base}/{page_id}/posts",
                params={"access_token": access_token, "fields": "id,created_time"}
            )
            posts = posts_resp.json().get("data", [])

            for post in posts:
                post_id = post["id"]
                # 2. Get comments on this post since last_checked
                comments_resp = await client.get(
                    f"{self.graph_api_base}/{post_id}/comments",
                    params={"access_token": access_token, "since": since_param, "fields": "id,message,from,created_time"}
                )
                comments = comments_resp.json().get("data", [])
                for comment in comments:
                    # 3. Check if already replied (optional: store replied comment IDs in your DB)
                    # For demo, let's assume you reply to all comments not by the page itself
                    if comment["from"]["id"] != page_id:
                        # 4. Generate reply (call your AI service)
                        reply_text = "Thank you for your comment! We appreciate your engagement. 😊"
                        # 5. Post reply
                        await client.post(
                            f"{self.graph_api_base}/{comment['id']}/comments",
                            data={"access_token": access_token, "message": reply_text}
                        )

    async def post_bulk_to_facebook(self, posts_data, page_id, access_token):
        """Post multiple posts to Facebook with proper media handling."""
        results = []
        
        for post_data in posts_data:
            try:
                caption = post_data.get('caption', '')
                media_file = post_data.get('media_file')
                scheduled_time = post_data.get('scheduled_time')
                
                # Determine post type based on media presence
                if media_file:
                    # Photo post with media
                    result = await self.post_photo_to_facebook(
                        page_id=page_id,
                        access_token=access_token,
                        message=caption,
                        image_data=media_file
                    )
                else:
                    # Text-only feed post
                    result = await self.post_text_to_facebook(
                        page_id=page_id,
                        access_token=access_token,
                        message=caption
                    )
                
                results.append({
                    'success': True,
                    'post_id': result.get('id'),
                    'caption': caption,
                    'post_type': 'photo' if media_file else 'feed'
                })
                
            except Exception as e:
                logger.error(f"Error posting to Facebook: {str(e)}")
                results.append({
                    'success': False,
                    'error': str(e),
                    'caption': post_data.get('caption', '')
                })
        
        return results

    async def post_photo_to_facebook(self, page_id, access_token, message, image_data):
        """Post a photo to Facebook."""
        try:
            # Remove data URL prefix if present
            if image_data.startswith('data:image'):
                # Extract the base64 data
                base64_data = image_data.split(',')[1]
            else:
                base64_data = image_data
            
            # Decode base64 to binary
            import base64
            image_binary = base64.b64decode(base64_data)
            
            # Create form data for multipart upload using aiohttp.FormData
            import io
            form_data = aiohttp.FormData()
            form_data.add_field('source', io.BytesIO(image_binary), filename='image.jpg', content_type='image/jpeg')
            form_data.add_field('message', message)
            form_data.add_field('access_token', access_token)
            
            url = f"https://graph.facebook.com/v20.0/{page_id}/photos"
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=form_data) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Successfully posted photo to Facebook: {result.get('id')}")
                        return result
                    else:
                        error_text = await response.text()
                        logger.error(f"Facebook photo post failed: {response.status} - {error_text}")
                        raise Exception(f"Facebook API error: {response.status} - {error_text}")
                        
        except Exception as e:
            logger.error(f"Error posting photo to Facebook: {str(e)}")
            raise

    async def post_text_to_facebook(self, page_id, access_token, message):
        """Post text-only content to Facebook feed."""
        try:
            url = f"https://graph.facebook.com/v20.0/{page_id}/feed"
            
            data = {
                'message': message,
                'access_token': access_token
            }
            
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.post(url, data=data) as response:
                    if response.status == 200:
                        result = await response.json()
                        logger.info(f"Successfully posted text to Facebook: {result.get('id')}")
                        return result
                    else:
                        error_text = await response.text()
                        logger.error(f"Facebook text post failed: {response.status} - {error_text}")
                        raise Exception(f"Facebook API error: {response.status} - {error_text}")
                        
        except Exception as e:
            logger.error(f"Error posting text to Facebook: {str(e)}")
            raise

    async def get_page_conversations(self, page_id: str, access_token: str) -> List[Dict[str, Any]]:
        """
        Fetch all conversations for a Facebook Page.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.graph_api_base}/{page_id}/conversations",
                    params={
                        "access_token": access_token,
                        "fields": "id,updated_time,senders,unread_count"
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("data", [])
                else:
                    logger.error(f"Failed to fetch conversations: {response.text}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching conversations: {e}")
            return []

    async def get_conversation_messages(self, conversation_id: str, access_token: str) -> List[Dict[str, Any]]:
        """
        Fetch messages in a conversation.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.graph_api_base}/{conversation_id}/messages",
                    params={
                        "access_token": access_token,
                        "fields": "id,from,message,created_time,to"
                    }
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("data", [])
                else:
                    logger.error(f"Failed to fetch messages: {response.text}")
                    return []
        except Exception as e:
            logger.error(f"Error fetching messages: {e}")
            return []

    async def send_message_reply(self, conversation_id: str, access_token: str, message: str) -> bool:
        """
        Send a reply to a conversation (Page message).
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.graph_api_base}/{conversation_id}/messages",
                    data={
                        "access_token": access_token,
                        "message": message
                    }
                )
                if response.status_code == 200:
                    logger.info(f"Successfully sent message reply to conversation {conversation_id}")
                    return True
                else:
                    logger.error(f"Failed to send message reply: {response.text}")
                    return False
        except Exception as e:
            logger.error(f"Error sending message reply: {e}")
            return False


# Create a singleton instance
facebook_service = FacebookService() 