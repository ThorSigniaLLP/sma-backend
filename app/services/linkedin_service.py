import requests
import logging
from typing import Dict, Any, Optional
from app.config import get_settings

logger = logging.getLogger(__name__)

class LinkedInService:
    def __init__(self):
        self.client_id = get_settings().linkedin_client_id
        self.client_secret = get_settings().linkedin_client_secret
        self.redirect_uri = get_settings().linkedin_redirect_uri
        self.api_base_url = "https://api.linkedin.com/v2"
        self.auth_base_url = "https://www.linkedin.com/oauth/v2"
    
    async def validate_access_token(self, access_token: str) -> Dict[str, Any]:
        """Validate LinkedIn access token and get user profile."""
        try:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            # Get user profile
            profile_response = requests.get(
                f"{self.api_base_url}/me",
                headers=headers
            )
            
            if profile_response.status_code != 200:
                logger.error(f"LinkedIn profile API error: {profile_response.status_code} - {profile_response.text}")
                return {
                    "valid": False,
                    "error": f"LinkedIn API error: {profile_response.status_code}"
                }
            
            profile_data = profile_response.json()
            
            # Get profile picture if available
            picture_response = requests.get(
                f"{self.api_base_url}/me?projection=(id,firstName,lastName,profilePicture(displayImage~:playableStreams))",
                headers=headers
            )
            
            profile_picture = None
            if picture_response.status_code == 200:
                picture_data = picture_response.json()
                if 'profilePicture' in picture_data and 'displayImage~' in picture_data['profilePicture']:
                    elements = picture_data['profilePicture']['displayImage~']['elements']
                    if elements:
                        profile_picture = elements[0]['identifiers'][0]['identifier']
            
            return {
                "valid": True,
                "id": profile_data.get('id'),
                "firstName": profile_data.get('localizedFirstName', ''),
                "lastName": profile_data.get('localizedLastName', ''),
                "name": f"{profile_data.get('localizedFirstName', '')} {profile_data.get('localizedLastName', '')}",
                "picture": profile_picture
            }
            
        except Exception as e:
            logger.error(f"Error validating LinkedIn token: {e}")
            return {
                "valid": False,
                "error": str(e)
            }
    
    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        """Exchange authorization code for access token."""
        try:
            token_url = f"{self.auth_base_url}/accessToken"
            data = {
                'grant_type': 'authorization_code',
                'code': code,
                'client_id': self.client_id,
                'client_secret': self.client_secret,
                'redirect_uri': self.redirect_uri
            }
            
            response = requests.post(token_url, data=data)
            response.raise_for_status()
            
            token_data = response.json()
            return {
                "access_token": token_data["access_token"],
                "expires_in": token_data.get("expires_in", 3600),
                "refresh_token": token_data.get("refresh_token")
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Error exchanging code for token: {str(e)}")
            raise Exception(f"Failed to exchange authorization code: {str(e)}")

    async def get_user_profile(self, access_token: str) -> Dict[str, Any]:
        """Get user profile information."""
        try:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            profile_url = f"{self.api_base_url}/me"
            response = requests.get(profile_url, headers=headers)
            response.raise_for_status()
            
            profile_data = response.json()
            return {
                "id": profile_data["id"],
                "firstName": profile_data["localizedFirstName"],
                "lastName": profile_data["localizedLastName"],
                "profilePicture": None  # LinkedIn API doesn't provide profile picture in basic profile
            }
        except requests.exceptions.RequestException as e:
            logger.error(f"Error getting user profile: {str(e)}")
            raise Exception(f"Failed to get user profile: {str(e)}")
    
    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh LinkedIn access token."""
        try:
            token_url = f"{self.auth_base_url}/accessToken"
            data = {
                'grant_type': 'refresh_token',
                'refresh_token': refresh_token,
                'client_id': self.client_id,
                'client_secret': self.client_secret
            }
            
            response = requests.post(token_url, data=data)
            
            if response.status_code != 200:
                logger.error(f"LinkedIn token refresh error: {response.status_code} - {response.text}")
                return {
                    "success": False,
                    "error": f"Token refresh failed: {response.status_code}"
                }
            
            token_data = response.json()
            
            return {
                "success": True,
                "access_token": token_data.get('access_token'),
                "expires_in": token_data.get('expires_in'),
                "refresh_token": token_data.get('refresh_token')
            }
            
        except Exception as e:
            logger.error(f"Error refreshing LinkedIn token: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def create_post(self, access_token: str, profile_id: str, content: str, image_url: Optional[str] = None) -> Dict[str, Any]:
        """Create a LinkedIn post."""
        try:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json',
                'X-Restli-Protocol-Version': '2.0.0'
            }
            
            # Prepare post data
            post_data = {
                "author": f"urn:li:person:{profile_id}",
                "lifecycleState": "PUBLISHED",
                "specificContent": {
                    "com.linkedin.ugc.ShareContent": {
                        "shareCommentary": {
                            "text": content
                        },
                        "shareMediaCategory": "NONE"
                    }
                },
                "visibility": {
                    "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
                }
            }
            
            # Add image if provided
            if image_url:
                post_data["specificContent"]["com.linkedin.ugc.ShareContent"]["shareMediaCategory"] = "IMAGE"
                post_data["specificContent"]["com.linkedin.ugc.ShareContent"]["media"] = [{
                    "status": "READY",
                    "description": {
                        "text": content
                    },
                    "media": image_url,
                    "title": {
                        "text": "LinkedIn Post"
                    }
                }]
            
            response = requests.post(
                f"{self.api_base_url}/ugcPosts",
                headers=headers,
                json=post_data
            )
            
            if response.status_code not in [200, 201]:
                logger.error(f"LinkedIn post creation error: {response.status_code} - {response.text}")
                return {
                    "success": False,
                    "error": f"Post creation failed: {response.status_code}"
                }
            
            post_data = response.json()
            
            return {
                "success": True,
                "post_id": post_data.get('id'),
                "message": "Post created successfully"
            }
            
        except Exception as e:
            logger.error(f"Error creating LinkedIn post: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_user_posts(self, access_token: str, profile_id: str, limit: int = 10) -> Dict[str, Any]:
        """Get user's LinkedIn posts."""
        try:
            headers = {
                'Authorization': f'Bearer {access_token}',
                'Content-Type': 'application/json'
            }
            
            response = requests.get(
                f"{self.api_base_url}/ugcPosts?authors=List({profile_id})&count={limit}",
                headers=headers
            )
            
            if response.status_code != 200:
                logger.error(f"LinkedIn posts fetch error: {response.status_code} - {response.text}")
                return {
                    "success": False,
                    "error": f"Posts fetch failed: {response.status_code}"
                }
            
            posts_data = response.json()
            
            return {
                "success": True,
                "posts": posts_data.get('elements', [])
            }
            
        except Exception as e:
            logger.error(f"Error fetching LinkedIn posts: {e}")
            return {
                "success": False,
                "error": str(e)
            }

# Create service instance
linkedin_service = LinkedInService() 