import requests
import logging
from typing import Dict, List, Optional, Tuple, Any
from datetime import datetime, timedelta
from app.config import get_settings
from app.services.groq_service import groq_service
from app.services.stability_service import stability_service
from app.services.cloudinary_service import cloudinary_service
import os
import time
import functools
from cachetools import TTLCache

# --- Instagram Auto-Reply Utilities ---
from app.models.social_account import SocialAccount
from app.database import get_db
import threading
from app.models.instagram_auto_reply_log import InstagramAutoReplyLog

# In-memory set for replied comment IDs (thread-safe)
_replied_comment_ids = set()
_replied_comment_ids_lock = threading.Lock()

logger = logging.getLogger(__name__)
settings = get_settings()

# Cache for API responses (5 minutes TTL)
_api_cache = TTLCache(maxsize=100, ttl=300)


def cache_api_response(func):
    """Decorator to cache API responses for 5 minutes."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        args_str = str(args)
        kwargs_str = str(sorted(kwargs.items()))
        cache_key = f"{func.__name__}:{hash(args_str + kwargs_str)}"
        
        if cache_key in _api_cache:
            logger.debug(f"Cache hit for {func.__name__}")
            return _api_cache[cache_key]
        
        result = func(*args, **kwargs)
        _api_cache[cache_key] = result
        return result
    return wrapper


class InstagramService:
    """Service for Instagram API operations and integrations."""
    
    def __init__(self):
        # Try v18.0 if v19.0 and v20.0 have issues
        self.graph_url = "https://graph.facebook.com/v18.0"
        self.app_id = settings.facebook_app_id
        self.app_secret = settings.facebook_app_secret
        self._session = requests.Session()
        self._session.timeout = 30
    
    def _make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request with error handling and retries."""
        max_retries = 3
        retry_delay = 1
        
        for attempt in range(max_retries):
            try:
                response = self._session.request(method, url, **kwargs)
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                if attempt == max_retries - 1:
                    raise e
                logger.warning(f"Request failed (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(retry_delay)
                retry_delay *= 2
        
        raise requests.exceptions.RequestException("All retry attempts failed")
    
    @cache_api_response
    def exchange_for_long_lived_token(self, short_lived_token: str, app_id: str, app_secret: str) -> Tuple[str, datetime]:
        """Exchange short-lived token for long-lived token (60 days)"""
        try:
            url = f"{self.graph_url}/oauth/access_token"
            params = {
                'grant_type': 'fb_exchange_token',
                'client_id': app_id,
                'client_secret': app_secret,
                'fb_exchange_token': short_lived_token
            }
            
            response = self._make_request('GET', url, params=params)
            data = response.json()
            long_lived_token = data['access_token']
            expires_in = data.get('expires_in', 5184000)
            expires_at = datetime.now() + timedelta(seconds=expires_in)
            
            logger.info("Successfully exchanged for long-lived token")
            return long_lived_token, expires_at
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Token exchange failed: {e}")
            raise Exception(f"Failed to exchange token: {str(e)}")
    
    @cache_api_response
    def verify_token_permissions(self, access_token: str) -> Dict:
        """Verify token has required permissions"""
        try:
            url = f"{self.graph_url}/me/permissions"
            params = {'access_token': access_token}
            
            response = self._make_request('GET', url, params=params)
            permissions_data = response.json()
            granted_permissions = [
                perm['permission'] for perm in permissions_data.get('data', [])
                if perm.get('status') == 'granted'
            ]
            
            required_permissions = [
                'pages_show_list',
                'instagram_basic', 
                'pages_read_engagement',
                'business_management'
            ]
            
            missing_permissions = [
                perm for perm in required_permissions 
                if perm not in granted_permissions
            ]
            
            return {
                'granted': granted_permissions,
                'missing': missing_permissions,
                'has_all_required': len(missing_permissions) == 0
            }
            
        except requests.exceptions.RequestException as e:
            if "400" in str(e):
                logger.info("Instagram token detected - /me/permissions not available for Instagram tokens")
                return {
                    'granted': ['instagram_basic', 'pages_read_engagement'],
                    'missing': [],
                    'has_all_required': True
                }
            else:
                logger.error(f"Permission verification failed: {e}")
                raise Exception(f"Failed to verify permissions: {str(e)}")
    
    @cache_api_response
    def get_facebook_pages_with_instagram(self, access_token: str) -> List[Dict]:
        """Get Facebook Pages with Instagram Business accounts"""
        try:
            perm_check = self.verify_token_permissions(access_token)
            if not perm_check['has_all_required']:
                missing = ', '.join(perm_check['missing'])
                raise Exception(f"Missing required permissions: {missing}. Please re-authorize the app.")
            
            url = f"{self.graph_url}/me/accounts"
            params = {
                'access_token': access_token,
                'fields': 'id,name,access_token,instagram_business_account{id,username,name,profile_picture_url,followers_count,media_count}'
            }
            
            response = self._make_request('GET', url, params=params)
            pages_data = response.json()
            pages = pages_data.get('data', [])
            
            if not pages:
                raise Exception("No Facebook Pages found. You need Admin access to at least one Facebook Page.")
            
            instagram_accounts = []
            pages_without_instagram = []
            
            for page in pages:
                page_name = page.get('name', 'Unknown Page')
                instagram_account = page.get('instagram_business_account')
                
                if instagram_account:
                    page_token = page.get('access_token')
                    if page_token:
                        enhanced_account = self._get_enhanced_instagram_details(
                            instagram_account['id'], 
                            page_token
                        )
                        
                        instagram_accounts.append({
                            'platform_id': instagram_account['id'],
                            'username': instagram_account.get('username', ''),
                            'display_name': instagram_account.get('name', ''),
                            'page_name': page_name,
                            'page_id': page['id'],
                            'followers_count': enhanced_account.get('followers_count', 0),
                            'media_count': enhanced_account.get('media_count', 0),
                            'profile_picture': enhanced_account.get('profile_picture_url', ''),
                            'page_access_token': page_token
                        })
                else:
                    pages_without_instagram.append(page_name)
            
            if not instagram_accounts:
                troubleshooting_msg = self._generate_troubleshooting_message(pages_without_instagram)
                raise Exception(troubleshooting_msg)
            
            logger.info(f"Found {len(instagram_accounts)} Instagram Business accounts")
            return instagram_accounts
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to fetch Instagram accounts: {e}")
            if hasattr(e, 'response') and e.response:
                error_data = e.response.json() if e.response.content else {}
                error_msg = error_data.get('error', {}).get('message', str(e))
                raise Exception(f"Graph API Error: {error_msg}")
            raise Exception(f"Network error: {str(e)}")
    
    def _get_enhanced_instagram_details(self, instagram_user_id: str, page_access_token: str) -> Dict:
        """Get additional Instagram account details"""
        try:
            url = f"{self.graph_url}/{instagram_user_id}"
            params = {
                'access_token': page_access_token,
                'fields': 'followers_count,media_count,profile_picture_url,biography'
            }
            
            response = self._make_request('GET', url, params=params)
            return response.json()
            
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to get enhanced Instagram details: {e}")
            return {}
    
    def _generate_troubleshooting_message(self, pages_without_instagram: List[str]) -> str:
        """Generate helpful troubleshooting message"""
        base_msg = "No Instagram Business accounts found."
        
        if pages_without_instagram:
            pages_list = ", ".join(pages_without_instagram)
            base_msg += f" Found Facebook Pages ({pages_list}) but no Instagram accounts connected."
        
        troubleshooting_steps = [
            "1. Convert your Instagram to Business/Creator account",
            "2. Go to Facebook Page Settings → Instagram → Connect Account", 
            "3. Ensure you have Admin/Editor role on the Facebook Page",
            "4. Verify Facebook Page is published (not draft)",
            "5. Wait 5-10 minutes after connecting for systems to sync",
            "6. Re-authorize this app if the connection is old"
        ]
        
        return f"{base_msg}\n\nTroubleshooting steps:\n" + "\n".join(troubleshooting_steps)
    
    async def create_post(self, instagram_user_id: str, page_access_token: str, 
                   caption: str, image_url: Optional[str] = None, video_url: Optional[str] = None, 
                   video_file_path: Optional[str] = None, video_filename: Optional[str] = None, is_reel: bool = False, 
                   thumbnail_url: Optional[str] = None, thumbnail_filename: Optional[str] = None,
                   thumbnail_file_path: Optional[str] = None) -> Dict:
        """Create Instagram post or reel"""
        try:
            # Validation
            if not all([instagram_user_id, page_access_token, caption]):
                return {"success": False, "error": "Missing required parameters"}
            
            # Validate Instagram user ID format (should be numeric)
            if not instagram_user_id.isdigit():
                return {"success": False, "error": "Invalid Instagram user ID format"}
            
            # Validate access token format (should be a long string)
            if len(page_access_token) < 50:
                return {"success": False, "error": "Invalid access token format"}
            
            logger.info(f"Creating Instagram post for user {instagram_user_id}")
            logger.info(f"Caption length: {len(caption)} characters")
            
            if video_filename and not video_file_path:
                video_file_path = os.path.join("temp_images", video_filename)
            
            # Media validation
            if is_reel and not (video_url or video_file_path):
                return {"success": False, "error": "Reel post requires a video file or URL."}
            elif not is_reel and not image_url:
                return {"success": False, "error": "Image post requires an image_url."}
            
            if len(caption) > 2200:
                return {"success": False, "error": f"Caption too long ({len(caption)} chars). Instagram limit is 2200 characters."}
            
            # Process video for reels
            final_video_url = None
            if is_reel:
                if video_file_path and os.path.exists(video_file_path):
                    upload_result = cloudinary_service.upload_video_with_instagram_transform(video_file_path)
                    if not upload_result["success"]:
                        return {"success": False, "error": f"Failed to upload video file: {upload_result.get('error', 'Unknown error')}"}
                    final_video_url = upload_result["url"]
                elif video_url and video_url.startswith(('http://', 'https://')):
                    final_video_url = video_url.strip()
                else:
                    return {"success": False, "error": "Invalid video URL or file for reel."}
            
            # Create media object
            media_url = f"{self.graph_url}/{instagram_user_id}/media"
            media_params = {
                'access_token': page_access_token,
                'caption': caption
            }
            
            if is_reel:
                media_params.update({
                    'video_url': final_video_url,
                    'media_type': 'REELS'
                })
                
                # Handle thumbnail for reels
                final_thumbnail_url = None
                if thumbnail_url and thumbnail_url.strip():
                    final_thumbnail_url = thumbnail_url.strip()
                elif thumbnail_file_path and os.path.exists(thumbnail_file_path):
                    upload_result = cloudinary_service.upload_image_with_instagram_transform(thumbnail_file_path)
                    if upload_result["success"]:
                        final_thumbnail_url = upload_result["url"]
                elif thumbnail_filename:
                    thumb_path = os.path.join("temp_images", thumbnail_filename)
                    if os.path.exists(thumb_path):
                        upload_result = cloudinary_service.upload_image_with_instagram_transform(thumb_path)
                        if upload_result["success"]:
                            final_thumbnail_url = upload_result["url"]
                
                if final_thumbnail_url:
                    media_params['cover_url'] = final_thumbnail_url
                    logger.info(f"Using thumbnail URL for reel: {final_thumbnail_url}")
            else:
                if not image_url or not image_url.strip():
                    return {"success": False, "error": "Image URL is required for photo posts"}
                if not image_url.startswith(('http://', 'https://')):
                    return {"success": False, "error": "Image URL must be a valid HTTP/HTTPS URL"}
                
                # Validate URL format
                try:
                    from urllib.parse import urlparse
                    parsed_url = urlparse(image_url)
                    if not parsed_url.scheme or not parsed_url.netloc:
                        return {"success": False, "error": "Invalid image URL format"}
                    
                    # Test if URL is accessible
                    import requests
                    try:
                        test_response = requests.head(image_url, timeout=10)
                        if test_response.status_code != 200:
                            logger.warning(f"Image URL returned status {test_response.status_code}: {image_url}")
                    except Exception as url_test_error:
                        logger.warning(f"Could not test image URL accessibility: {url_test_error}")
                        
                except Exception as e:
                    return {"success": False, "error": f"Invalid image URL: {str(e)}"}
                
                media_params['image_url'] = image_url.strip()
                logger.info(f"Using image URL: {image_url}")
            
            # Create media
            logger.info(f"Creating Instagram media with params: {media_params}")
            logger.info(f"Media URL: {media_url}")
            
            try:
                response = self._make_request('POST', media_url, data=media_params)
                media_result = response.json()
                logger.info(f"Media creation response: {media_result}")
                creation_id = media_result.get('id')
            except requests.exceptions.RequestException as e:
                logger.error(f"Instagram media creation failed: {e}")
                if hasattr(e, 'response') and e.response:
                    error_data = e.response.json() if e.response.content else {}
                    error_msg = error_data.get('error', {}).get('message', str(e))
                    logger.error(f"Instagram API error details: {error_data}")
                    return {"success": False, "error": f"Instagram API Error: {error_msg}"}
                raise e
            
            if not creation_id:
                return {"success": False, "error": "No creation ID returned from Instagram API."}
            
            # Publish media
            publish_url = f"{self.graph_url}/{instagram_user_id}/media_publish"
            publish_params = {
                'access_token': page_access_token,
                'creation_id': creation_id
            }
            
            # For reels, wait for processing
            if is_reel:
                max_attempts = 10
                for attempt in range(max_attempts):
                    status_response = self._make_request('GET', f"{self.graph_url}/{creation_id}", 
                                                      params={'access_token': page_access_token, 'fields': 'status_code'})
                    status_data = status_response.json()
                    if status_data.get('status_code') in ('FINISHED', 'READY', 'PUBLISHED'):
                        break
                    time.sleep(3)
                else:
                    return {"success": False, "error": "Media not ready to publish after waiting."}
            
            publish_response = self._make_request('POST', publish_url, data=publish_params)
            publish_result = publish_response.json()
            
            return {
                "success": True, 
                "post_id": publish_result.get('id'), 
                "creation_id": creation_id,
                "reel_thumbnail_url": final_thumbnail_url if is_reel else None
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error creating Instagram post: {e}")
            return {"success": False, "error": f"Network error: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error creating Instagram post: {e}")
            return {"success": False, "error": f"Unexpected error: {str(e)}"}
    
    def get_user_media(self, instagram_user_id: str, page_access_token: str, limit: int = 25) -> List[Dict]:
        """Get user's Instagram media"""
        try:
            url = f"{self.graph_url}/{instagram_user_id}/media"
            params = {
                'access_token': page_access_token,
                'fields': 'id,media_type,media_url,thumbnail_url,caption,timestamp,permalink',
                'limit': limit
            }
            
            response = self._make_request('GET', url, params=params)
            media_data = response.json()
            return media_data.get('data', [])
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get user media: {e}")
            return []
    
    async def generate_instagram_image_with_ai(self, prompt: str, post_type: str = "feed") -> Dict[str, Any]:
        """Generate an image optimized for Instagram using Stability AI."""
        try:
            dimensions = {
                "feed": (1024, 1024),
                "story": (832, 1216),
                "square": (1024, 1024),
                "portrait": (896, 1152),
                "landscape": (1152, 896)
            }
            
            width, height = dimensions.get(post_type, dimensions["feed"])
            enhanced_prompt = f"High-quality, Instagram-worthy image: {prompt}, vibrant colors, good lighting, visually appealing, social media optimized"
            negative_prompt = "blurry, low quality, distorted, text overlay, watermark, ugly, bad anatomy, low resolution"
            
            image_result = await stability_service.generate_image(
                prompt=enhanced_prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                cfg_scale=8.0,
                steps=40,
                samples=1
            )
            
            if not image_result["success"]:
                return {
                    "success": False,
                    "error": f"Image generation failed: {image_result.get('error', 'Unknown error')}"
                }
            
            return {
                "success": True,
                "image_base64": image_result["image_base64"],
                "prompt": prompt,
                "enhanced_prompt": enhanced_prompt,
                "width": width,
                "height": height,
                "post_type": post_type
            }
            
        except Exception as e:
            logger.error(f"Error generating Instagram image with AI: {e}")
            return {"success": False, "error": str(e)}
    
    async def create_carousel_post(self, instagram_user_id: str, page_access_token: str, 
                                  caption: str, image_urls: List[str]) -> Dict[str, Any]:
        """Create an Instagram carousel post with multiple images."""
        try:
            if not all([instagram_user_id, page_access_token, caption, image_urls]):
                return {"success": False, "error": "Missing required parameters"}
            
            if len(image_urls) < 3 or len(image_urls) > 7:
                return {"success": False, "error": "Carousel must have between 3 and 7 images"}
            
            if len(caption) > 2200:
                return {"success": False, "error": f"Caption too long ({len(caption)} chars). Instagram limit is 2200 characters."}
            
            # Validate image URLs
            for i, url in enumerate(image_urls):
                if not url.startswith(('http://', 'https://')):
                    return {"success": False, "error": f"Image {i+1} URL must be a valid HTTP/HTTPS URL"}
            
            # Create child media objects
            children_creation_ids = []
            for url in image_urls:
                child_response = self._make_request('POST', f"{self.graph_url}/{instagram_user_id}/media", data={
                    'access_token': page_access_token,
                    'image_url': url,
                    'is_carousel_item': 'true'
                })
                children_creation_ids.append(child_response.json()['id'])
            
            # Create carousel container
            media_url = f"{self.graph_url}/{instagram_user_id}/media"
            media_params = {
                'access_token': page_access_token,
                'caption': caption,
                'media_type': 'CAROUSEL'
            }
            
            for idx, cid in enumerate(children_creation_ids):
                media_params[f'children[{idx}]'] = cid
            
            media_response = self._make_request('POST', media_url, data=media_params)
            media_data = media_response.json()
            creation_id = media_data['id']
            
            # Publish carousel
            publish_url = f"{self.graph_url}/{instagram_user_id}/media_publish"
            publish_params = {
                'access_token': page_access_token,
                'creation_id': creation_id
            }
            
            publish_response = self._make_request('POST', publish_url, data=publish_params)
            publish_data = publish_response.json()
            
            return {
                'success': True,
                'post_id': publish_data['id'],
                'creation_id': creation_id,
                'image_count': len(image_urls)
            }
            
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to create Instagram carousel: {e}")
            if hasattr(e, 'response') and e.response:
                try:
                    error_data = e.response.json()
                    error_msg = error_data.get('error', {}).get('message', str(e))
                except ValueError:
                    error_msg = str(e)
                return {'success': False, 'error': f"Carousel creation failed: {error_msg}"}
            return {'success': False, 'error': f"Network error: {str(e)}"}
        except Exception as e:
            logger.error(f"Unexpected error creating Instagram carousel: {e}")
            return {"success": False, "error": f"Unexpected error: {str(e)}"}
    
    async def get_comments(self, instagram_user_id: str, page_access_token: str, 
                          media_id: str = None, limit: int = 25) -> List[Dict]:
        """Get comments for Instagram media."""
        try:
            if media_id:
                url = f"{self.graph_url}/{media_id}/comments"
                params = {
                    'access_token': page_access_token,
                    'fields': 'id,text,from,timestamp',
                    'limit': limit
                }
                
                response = self._make_request('GET', url, params=params)
                data = response.json()
                return data.get('data', [])
            else:
                # Get recent media first, then get comments for each media
                url = f"{self.graph_url}/{instagram_user_id}/media"
                params = {
                    'access_token': page_access_token,
                    'fields': 'id,caption,media_type,media_url',
                    'limit': limit
                }
                
                response = self._make_request('GET', url, params=params)
                media_data = response.json()
                media_list = media_data.get('data', [])
                
                all_comments = []
                for media in media_list:
                    media_id = media['id']
                    comments_url = f"{self.graph_url}/{media_id}/comments"
                    comments_params = {
                        'access_token': page_access_token,
                        'fields': 'id,text,from,timestamp',
                        'limit': 10
                    }
                    
                    try:
                        comments_response = self._make_request('GET', comments_url, params=comments_params)
                        comments_data = comments_response.json()
                        comments = comments_data.get('data', [])
                        
                        for comment in comments:
                            comment['media_id'] = media_id
                        
                        all_comments.extend(comments)
                    except requests.exceptions.RequestException as e:
                        logger.warning(f"Failed to get comments for media {media_id}: {e}")
                        continue
                
                return all_comments
                
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to get Instagram comments: {e}")
            return []
    
    async def reply_to_comment(self, comment_id: str, page_access_token: str, message: str) -> dict:
        """Reply to an Instagram comment using the Graph API."""
        try:
            url = f"{self.graph_url}/{comment_id}/replies"
            data = {
                'access_token': page_access_token,
                'message': message
            }
            # Use requests in a thread pool for async compatibility
            import asyncio
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: self._session.post(url, data=data, timeout=30)
            )
            response.raise_for_status()
            result = response.json()
            return {"success": True, "id": result.get("id")}
        except Exception as e:
            logger.error(f"Failed to reply to Instagram comment {comment_id}: {e}")
            return {"success": False, "error": str(e)}
    
    def is_configured(self) -> bool:
        """Check if Instagram service is properly configured."""
        return bool(self.app_id and self.app_secret)


# Create a singleton instance
instagram_service = InstagramService() 

def get_access_token_for_user(instagram_user_id: str):
    """Get the page access token for a given Instagram user ID from the SocialAccount table."""
    db = next(get_db())
    account = db.query(SocialAccount).filter_by(platform="instagram", platform_user_id=instagram_user_id).first()
    if account and account.platform_data:
        return account.platform_data.get("page_access_token")
    return None

async def has_auto_reply(comment_id: str, instagram_user_id: str, db) -> bool:
    return db.query(InstagramAutoReplyLog).filter_by(comment_id=comment_id, instagram_user_id=instagram_user_id).first() is not None

async def mark_auto_replied(comment_id: str, instagram_user_id: str, db):
    if not await has_auto_reply(comment_id, instagram_user_id, db):
        log = InstagramAutoReplyLog(comment_id=comment_id, instagram_user_id=instagram_user_id)
        db.add(log)
        db.commit()
# NOTE: For production, implement persistent storage for replied comment IDs. 