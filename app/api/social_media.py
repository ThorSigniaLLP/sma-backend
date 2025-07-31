from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Body, Query, BackgroundTasks
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.api.auth import get_current_user
from app.models.user import User
from app.models.social_account import SocialAccount
from app.models.post import Post, PostStatus
from app.models.post import PostType as PostPostType  # Rename to avoid conflict
from app.models.automation_rule import AutomationRule, RuleType, TriggerType
from app.models.bulk_composer_content import BulkComposerContent, BulkComposerStatus
from app.schemas.social_media import (
    SocialAccountResponse, PostCreate, PostResponse, PostUpdate,
    AutomationRuleCreate, AutomationRuleResponse, AutomationRuleUpdate,
    FacebookConnectRequest, FacebookPostRequest, AutoReplyToggleRequest,
    InstagramConnectRequest, InstagramPostRequest,
    InstagramAutoReplyToggleRequest, SuccessResponse,
    LinkedInConnectRequest
)
from pydantic import BaseModel, Field, model_validator
from datetime import datetime, timedelta, timezone
import logging
from app.services.instagram_service import instagram_service
from app.services.cloudinary_service import cloudinary_service
from uuid import uuid4
from app.services.linkedin_service import LinkedInService
import pytz
from app.models.dm_auto_reply_status import DmAutoReplyStatus
from app.models.scheduled_post import ScheduledPost, PostType, FrequencyType
from app.models.single_instagram_post import SingleInstagramPost


# Request Models
class ImageGenerationRequest(BaseModel):
    """Request model for image generation."""
    image_prompt: str = Field(..., min_length=1, max_length=500, description="Prompt for image generation")
    post_type: str = Field(default="feed", description="Type of post for sizing (feed, story, square, etc.)")


class UnifiedFacebookPostRequest(BaseModel):
    """Unified request model for creating Facebook posts with various options."""
    page_id: str = Field(..., description="Facebook page ID")
    text_content: Optional[str] = Field(None, description="Text content for the post (if not using AI generation)")
    content_prompt: Optional[str] = Field(None, description="Prompt for AI text generation")
    image_prompt: Optional[str] = Field(None, description="Prompt for AI image generation")
    image_url: Optional[str] = Field(None, description="URL of existing image to use")
    video_url: Optional[str] = Field(None, description="URL of existing video to use (base64 data URL)")
    post_type: str = Field(default="feed", description="Type of post for sizing")
    use_ai_text: bool = Field(default=False, description="Whether to generate text using AI")
    use_ai_image: bool = Field(default=False, description="Whether to generate image using AI")
    
    @model_validator(mode='after')
    def validate_content_requirements(self):
        """Ensure at least one content source is provided."""
        has_content = any([
            self.text_content and self.text_content.strip(),
            self.content_prompt and self.content_prompt.strip(),
            self.image_url and self.image_url.strip(),
            self.image_prompt and self.image_prompt.strip(),
            self.video_url and self.video_url.strip()
        ])
        
        if not has_content:
            raise ValueError("At least one of text_content, content_prompt, image_url, image_prompt, or video_url must be provided")
        
        return self


class InstagramCarouselRequest(BaseModel):
    """Request model for Instagram carousel generation and posting."""
    image_prompt: str = Field(..., min_length=1, max_length=500, description="Prompt for carousel images")
    count: int = Field(default=3, ge=3, le=7, description="Number of images to generate (3-7)")
    post_type: str = Field(default="feed", description="Type of post for sizing (feed, story, square, etc.)")


class InstagramCarouselPostRequest(BaseModel):
    """Request model for Instagram carousel posting."""
    instagram_user_id: str = Field(..., description="Instagram user ID")
    caption: str = Field(..., min_length=1, max_length=2200, description="Caption for the carousel")
    image_urls: List[str] = Field(..., min_items=3, max_items=7, description="List of image URLs (3-7 images)")


class BulkComposerPost(BaseModel):
    """Individual post data for bulk composer."""
    caption: str = Field(..., description="Post caption")
    scheduled_date: str = Field(..., description="Scheduled date (YYYY-MM-DD)")
    scheduled_time: str = Field(..., description="Scheduled time (HH:MM)")
    media_file: Optional[str] = Field(None, description="Base64 encoded media file")
    media_filename: Optional[str] = Field(None, description="Media filename")


class BulkComposerRequest(BaseModel):
    """Request model for bulk composer."""
    social_account_id: int = Field(..., description="Social account ID")
    posts: List[BulkComposerPost] = Field(..., min_items=1, description="List of posts to schedule")


class UnifiedInstagramPostRequest(BaseModel):
    """Unified request model for creating Instagram posts with various options."""
    instagram_user_id: str = Field(..., description="Instagram user ID")
    caption: Optional[str] = Field(None, description="Text caption for the post")
    content_prompt: Optional[str] = Field(None, description="Prompt for AI text generation")
    image_prompt: Optional[str] = Field(None, description="Prompt for AI image generation")
    image_url: Optional[str] = Field(None, description="URL of existing image to use")
    video_url: Optional[str] = Field(None, description="URL of existing video to use (base64 data URL)")
    video_filename: Optional[str] = Field(None, description="Filename of existing video")
    media_file: Optional[str] = Field(None, description="Base64 encoded media file (for video uploads)")
    media_filename: Optional[str] = Field(None, description="Media filename (for video uploads)")
    post_type: str = Field(default="feed", description="Type of post for sizing")
    use_ai_text: bool = Field(default=False, description="Whether to generate text using AI")
    use_ai_image: bool = Field(default=False, description="Whether to generate image using AI")
    media_type: str = Field(default="image", description="Type of media (image, video, carousel)")
    thumbnail_url: Optional[str] = None
    thumbnail_filename: Optional[str] = None
    thumbnail_file: Optional[str] = None
    
    @model_validator(mode='after')
    def validate_content_requirements(self):
        """Ensure at least one content source is provided."""
        has_content = any([
            self.caption and self.caption.strip(),
            self.content_prompt and self.content_prompt.strip(),
            self.image_url and self.image_url.strip(),
            self.image_prompt and self.image_prompt.strip(),
            self.video_url and self.video_url.strip(),
            self.video_filename and self.video_filename.strip(),
            self.media_file and self.media_file.strip(),
            self.media_filename and self.media_filename.strip()
        ])
        
        if not has_content:
            raise ValueError("At least one of caption, content_prompt, image_url, image_prompt, video_url, video_filename, media_file, or media_filename must be provided")
        
        return self


class CustomStrategyCaptionRequest(BaseModel):
    """Request model for generating captions using custom strategy templates."""
    custom_strategy: str = Field(..., min_length=1, max_length=2000, description="Custom strategy template")
    context: Optional[str] = Field("", description="Additional context or topic for the caption")
    max_length: int = Field(default=2000, ge=100, le=5000, description="Maximum character length for the caption")


class BulkCaptionGenerationRequest(BaseModel):
    """Request model for generating captions for multiple posts."""
    custom_strategy: str = Field(..., min_length=1, max_length=2000, description="Custom strategy template")
    contexts: List[str] = Field(..., min_items=1, description="List of contexts for each caption")
    max_length: int = Field(default=2000, ge=100, le=5000, description="Maximum character length for the captions")


class FacebookCustomStrategyCaptionRequest(BaseModel):
    """Request model for generating Facebook captions using custom strategy templates."""
    custom_strategy: str = Field(..., min_length=1, max_length=2000, description="Custom strategy template")
    context: Optional[str] = Field("", description="Additional context or topic for the caption")
    max_length: int = Field(default=2000, ge=100, le=5000, description="Maximum character length for the caption")


class InstagramImageGenerationRequest(BaseModel):
    """Request model for Instagram image generation."""
    image_prompt: str = Field(..., min_length=1, max_length=500, description="Prompt for image generation")
    post_type: str = Field(default="feed", description="Type of post for sizing (feed, story, square, etc.)")


class InstagramCarouselGenerationRequest(BaseModel):
    """Request model for Instagram carousel generation."""
    image_prompt: str = Field(..., min_length=1, max_length=500, description="Prompt for carousel images")
    count: int = Field(default=3, ge=3, le=7, description="Number of images to generate (3-7)")
    post_type: str = Field(default="feed", description="Type of post for sizing (feed, story, square, etc.)")


class BulkComposerUpdateRequest(BaseModel):
    caption: str = Field(..., description="Updated post caption")


router = APIRouter(tags=["social media"])

logger = logging.getLogger(__name__)


# Social Account Management
@router.get("/social/accounts", response_model=List[SocialAccountResponse])
async def get_social_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all connected social accounts for the current user."""
    try:
        accounts = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.is_connected == True
        ).all()
        result = []
        for acc in accounts:
            try:
                media_count = db.query(Post).filter(
                    Post.social_account_id == acc.id,
                    Post.status == PostStatus.PUBLISHED
                ).count()
                
                # Create a clean dict with proper defaults for None values
                acc_dict = {
                    "id": acc.id,
                    "user_id": acc.user_id,
                    "platform": acc.platform,
                    "platform_user_id": acc.platform_user_id,
                    "username": acc.username,
                    "display_name": acc.display_name,
                    "profile_picture_url": acc.profile_picture_url,
                    "follower_count": acc.follower_count or 0,
                    "account_type": acc.account_type,
                    "is_verified": acc.is_verified or False,
                    "is_active": acc.is_active or True,
                    "is_connected": acc.is_connected or True,
                    "connected_at": acc.connected_at,
                    "last_sync_at": acc.last_sync_at,
                    "media_count": media_count
                }
                
                result.append(SocialAccountResponse(**acc_dict))
            except Exception as e:
                logger.error(f"Error processing social account {acc.id}: {str(e)}")
                continue
                
        return result
    except Exception as e:
        logger.error(f"Error fetching social accounts for user {current_user.id}: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to fetch social accounts: {str(e)}")


@router.get("/social/accounts/{account_id}", response_model=SocialAccountResponse)
async def get_social_account(
    account_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get a specific social media account."""
    account = db.query(SocialAccount).filter(
        SocialAccount.id == account_id,
        SocialAccount.user_id == current_user.id
    ).first()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Social account not found"
        )
    
    return account


# Facebook Integration
@router.get("/social/facebook/status")
async def get_facebook_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Check if user has existing Facebook connections and ensure AUTO_REPLY rule is present/enabled for each page."""
    from app.models.automation_rule import AutomationRule, RuleType, TriggerType
    from app.services.facebook_service import facebook_service
    facebook_accounts = db.query(SocialAccount).filter(
        SocialAccount.user_id == current_user.id,
        SocialAccount.platform == "facebook",
        SocialAccount.is_connected == True
    ).all()
    
    if not facebook_accounts:
        return {
            "connected": False,
            "message": "No Facebook accounts connected"
        }
    
    # Separate personal accounts from pages
    personal_accounts = [acc for acc in facebook_accounts if acc.account_type == "personal"]
    page_accounts = [acc for acc in facebook_accounts if acc.account_type == "page"]

    # --- Ensure AUTO_REPLY rule is present and enabled for each page ---
    for acc in page_accounts:
        # Fetch latest page info from Facebook
        try:
            page_info = None
            if acc.access_token:
                async with __import__('httpx').AsyncClient() as client:
                    resp = await client.get(
                        f"https://graph.facebook.com/v23.0/{acc.platform_user_id}",
                        params={
                            "fields": "fan_count,name,picture",
                            "access_token": acc.access_token
                        }
                    )
                    if resp.status_code == 200:
                        page_info = resp.json()
            if page_info:
                acc.follower_count = page_info.get("fan_count", acc.follower_count)
                acc.display_name = page_info.get("name", acc.display_name)
                acc.profile_picture_url = page_info.get("picture", {}).get("data", {}).get("url", acc.profile_picture_url)
                db.commit()
        except Exception as e:
            logger.warning(f"Could not update follower count for page {acc.platform_user_id}: {e}")
        # Ensure AUTO_REPLY rule
        auto_reply_rule = db.query(AutomationRule).filter(
            AutomationRule.user_id == current_user.id,
            AutomationRule.social_account_id == acc.id,
            AutomationRule.rule_type == RuleType.AUTO_REPLY
        ).first()
        if auto_reply_rule:
            if not auto_reply_rule.is_active:
                auto_reply_rule.is_active = True
                db.commit()
        else:
            # Create new AUTO_REPLY rule for this page
            auto_reply_rule = AutomationRule(
                user_id=current_user.id,
                social_account_id=acc.id,
                name=f"Auto Reply - {acc.display_name}",
                rule_type=RuleType.AUTO_REPLY,
                trigger_type=TriggerType.ENGAGEMENT_BASED,
                trigger_conditions={
                    "event": "comment",
                    "selected_posts": []  # Empty means all posts
                },
                actions={
                    "ai_enabled": True,
                    "selected_facebook_post_ids": []  # Empty means all posts
                },
                is_active=True
            )
            db.add(auto_reply_rule)
            db.commit()
    # --- End ensure AUTO_REPLY rule ---
    
    return {
        "connected": True,
        "message": f"Found {len(facebook_accounts)} Facebook connection(s)",
        "accounts": {
            "personal": [{
                "id": acc.id,
                "platform_id": acc.platform_user_id,
                "name": acc.display_name or "Personal Profile",
                "profile_picture": acc.profile_picture_url,
                "connected_at": acc.connected_at.isoformat() if acc.connected_at else None
            } for acc in personal_accounts],
            "pages": [{
                "id": acc.id,
                "platform_id": acc.platform_user_id,
                "name": acc.display_name,
                "category": acc.platform_data.get("category", "Page") if acc.platform_data else "Page",
                "profile_picture": acc.profile_picture_url,
                "follower_count": acc.follower_count or 0,
                "can_post": acc.platform_data.get("can_post", True) if acc.platform_data else True,
                "can_comment": acc.platform_data.get("can_comment", True) if acc.platform_data else True,
                "connected_at": acc.connected_at.isoformat() if acc.connected_at else None
            } for acc in page_accounts]
        },
        "total_accounts": len(facebook_accounts),
        "pages_count": len(page_accounts)
    }


@router.post("/social/facebook/logout")
async def logout_facebook(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Disconnect all Facebook accounts for the user."""
    try:
        # Find all Facebook accounts for this user
        facebook_accounts = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook"
        ).all()
        
        if not facebook_accounts:
            return SuccessResponse(
                message="No Facebook accounts to disconnect"
            )
        
        # Mark all as disconnected and clear sensitive data
        disconnected_count = 0
        for account in facebook_accounts:
            account.is_connected = False
            account.access_token = ""  # Clear the token for security
            account.last_sync_at = datetime.now()
            disconnected_count += 1
        
        db.commit()
        
        logger.info(f"User {current_user.id} disconnected {disconnected_count} Facebook accounts")
        
        return SuccessResponse(
            message=f"Successfully disconnected {disconnected_count} Facebook account(s)",
            data={
                "disconnected_accounts": disconnected_count,
                "timestamp": datetime.utcnow().isoformat()
            }
        )
        
    except Exception as e:
        logger.error(f"Error disconnecting Facebook accounts for user {current_user.id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to disconnect Facebook accounts"
        )


@router.post("/social/facebook/connect")
async def connect_facebook(
    request: FacebookConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Connect Facebook account and pages."""
    try:
        from app.services.facebook_service import facebook_service
        
        logger.info(f"Facebook connect request for user {current_user.id}: {request.user_id}")
        logger.info(f"Pages data received: {len(request.pages or [])} pages")
        
        # Exchange short-lived token for long-lived token
        logger.info("Exchanging short-lived token for long-lived token...")
        token_exchange_result = await facebook_service.exchange_for_long_lived_token(request.access_token)
        
        if not token_exchange_result["success"]:
            logger.error(f"Token exchange failed: {token_exchange_result.get('error')}")
            raise HTTPException(
                status_code=400,
                detail=f"Failed to get long-lived token: {token_exchange_result.get('error')}"
            )
        
        long_lived_token = token_exchange_result["access_token"]
        expires_at = token_exchange_result["expires_at"]
        
        logger.info(f"Successfully got long-lived token, expires at: {expires_at}")
        
        # Validate the new long-lived token
        validation_result = await facebook_service.validate_access_token(long_lived_token)
        if not validation_result["valid"]:
            raise HTTPException(
                status_code=400,
                detail=f"Long-lived token validation failed: {validation_result.get('error')}"
            )
        
        # Check if account already exists
        existing_account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook",
            SocialAccount.platform_user_id == request.user_id
        ).first()
        
        if existing_account:
            # Update existing account with long-lived token
            existing_account.access_token = long_lived_token
            existing_account.token_expires_at = expires_at
            existing_account.is_connected = True
            existing_account.last_sync_at = datetime.utcnow()
            existing_account.display_name = validation_result.get("name")
            existing_account.profile_picture_url = validation_result.get("picture")
            db.commit()
            account = existing_account
        else:
            # Create new account with long-lived token
            account = SocialAccount(
                user_id=current_user.id,
                platform="facebook",
                platform_user_id=request.user_id,
                access_token=long_lived_token,
                token_expires_at=expires_at,
                account_type="personal",
                display_name=validation_result.get("name"),
                profile_picture_url=validation_result.get("picture"),
                is_connected=True,
                last_sync_at=datetime.utcnow()
            )
            db.add(account)
            db.commit()
            db.refresh(account)
        
        # Handle pages if provided - get long-lived page tokens
        connected_pages = []
        if request.pages:
            logger.info(f"Processing {len(request.pages)} Facebook pages with long-lived tokens")
            
            # Get long-lived page tokens
            long_lived_pages = await facebook_service.get_long_lived_page_tokens(long_lived_token)
            
            # Create a mapping of page IDs to long-lived tokens
            page_token_map = {page["id"]: page["access_token"] for page in long_lived_pages}
            
            for page_data in request.pages:
                # Ensure we have a dict so we can use .get safely
                if hasattr(page_data, "dict"):
                    page_data = page_data.dict()

                page_id = page_data.get("id")
                page_access_token = page_token_map.get(page_id, page_data.get("access_token", ""))
                
                if not page_access_token:
                    logger.warning(f"No access token found for page {page_id}")
                    continue
                
                # Check if page account already exists
                existing_page = db.query(SocialAccount).filter(
                    SocialAccount.user_id == current_user.id,
                    SocialAccount.platform == "facebook",
                    SocialAccount.platform_user_id == page_id
                ).first()
                
                if existing_page:
                    # Update existing page account
                    existing_page.access_token = page_access_token
                    existing_page.token_expires_at = None  # Page tokens don't expire
                    existing_page.display_name = page_data.get("name", existing_page.display_name)
                    existing_page.profile_picture_url = page_data.get("picture", {}).get("data", {}).get("url", existing_page.profile_picture_url)
                    existing_page.follower_count = page_data.get("fan_count", existing_page.follower_count)
                    existing_page.is_connected = True
                    existing_page.last_sync_at = datetime.utcnow()
                    existing_page.platform_data = {
                        "category": page_data.get("category"),
                        "tasks": page_data.get("tasks", []),
                        "can_post": "CREATE_CONTENT" in page_data.get("tasks", []),
                        "can_comment": "MODERATE" in page_data.get("tasks", [])
                    }
                    page_account = existing_page
                else:
                    # Create new page account
                    page_account = SocialAccount(
                        user_id=current_user.id,
                        platform="facebook",
                        platform_user_id=page_id,
                        username=page_data.get("name", "").replace(" ", "").lower(),
                        display_name=page_data.get("name"),
                        access_token=page_access_token,
                        token_expires_at=None,  # Page tokens don't expire
                        profile_picture_url=page_data.get("picture", {}).get("data", {}).get("url"),
                        follower_count=page_data.get("fan_count", 0),
                        account_type="page",
                        platform_data={
                            "category": page_data.get("category"),
                            "tasks": page_data.get("tasks", []),
                            "can_post": "CREATE_CONTENT" in page_data.get("tasks", []),
                            "can_comment": "MODERATE" in page_data.get("tasks", [])
                        },
                        is_connected=True,
                        last_sync_at=datetime.utcnow()
                    )
                    db.add(page_account)
                
                if not hasattr(page_account, 'id') or page_account.id is None:
                    logger.error(f"Page account for page_id={page_id} has no id after creation. Skipping automation rule creation for this page.")
                    continue
                # --- Ensure auto-reply rule is created and enabled for this page ---
                from app.models.automation_rule import AutomationRule, RuleType, TriggerType
                auto_reply_rule = db.query(AutomationRule).filter(
                    AutomationRule.user_id == current_user.id,
                    AutomationRule.social_account_id == page_account.id,
                    AutomationRule.rule_type == RuleType.AUTO_REPLY
                ).first()
                if auto_reply_rule:
                    auto_reply_rule.is_active = True
                    # Set to all posts by default
                    auto_reply_rule.actions = auto_reply_rule.actions or {}
                    auto_reply_rule.actions["ai_enabled"] = True
                    auto_reply_rule.actions["selected_facebook_post_ids"] = []  # Empty means all posts
                else:
                    auto_reply_rule = AutomationRule(
                        user_id=current_user.id,
                        social_account_id=page_account.id,
                        name=f"Auto Reply - {page_account.display_name}",
                        rule_type=RuleType.AUTO_REPLY,
                        trigger_type=TriggerType.ENGAGEMENT_BASED,
                        trigger_conditions={
                            "event": "comment",
                            "selected_posts": []  # Empty means all posts
                        },
                        actions={
                            "ai_enabled": True,
                            "selected_facebook_post_ids": []  # Empty means all posts
                        },
                        is_active=True
                    )
                    db.add(auto_reply_rule)
                # --- End auto-reply rule logic ---
                
                # --- Ensure auto-reply MESSAGE rule is created and enabled for this page ---
                auto_reply_msg_rule = db.query(AutomationRule).filter(
                    AutomationRule.user_id == current_user.id,
                    AutomationRule.social_account_id == page_account.id,
                    AutomationRule.rule_type == RuleType.AUTO_REPLY_MESSAGE.value
                ).first()
                if auto_reply_msg_rule:
                    auto_reply_msg_rule.is_active = True
                    auto_reply_msg_rule.actions = auto_reply_msg_rule.actions or {}
                    auto_reply_msg_rule.actions["ai_enabled"] = True
                    auto_reply_msg_rule.actions["message_template"] = "Thank you for your message! We'll get back to you soon."
                else:
                    auto_reply_msg_rule = AutomationRule(
                        user_id=current_user.id,
                        social_account_id=page_account.id,
                        name=f"Auto Reply Message - {page_account.display_name}",
                        rule_type=RuleType.AUTO_REPLY_MESSAGE.value,
                        trigger_type=TriggerType.ENGAGEMENT_BASED,
                        trigger_conditions={
                            "event": "message"
                        },
                        actions={
                            "ai_enabled": True,
                            "message_template": "Thank you for your message! We'll get back to you soon."
                        },
                        is_active=True
                    )
                    db.add(auto_reply_msg_rule)
                # --- End auto-reply MESSAGE rule logic ---
                
                connected_pages.append({
                    "id": page_id,
                    "name": page_data.get("name"),
                    "category": page_data.get("category"),
                    "access_token_type": "long_lived_page_token"
                })
        
        db.commit()
        
        logger.info(f"Successfully connected Facebook account {request.user_id} with {len(connected_pages)} pages")
        
        return {
            "success": True,
            "message": f"Facebook account connected successfully with long-lived tokens",
            "data": {
                "account_id": account.id,
                "user_id": request.user_id,
                "pages_connected": len(connected_pages),
                "pages": connected_pages,
                "token_type": "long_lived_user_token",
                "token_expires_at": expires_at.isoformat() if expires_at else None
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting Facebook account: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to connect Facebook account: {str(e)}"
        )


@router.post("/social/facebook/post")
async def create_facebook_post(
    request: FacebookPostRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create and schedule a Facebook post with AI integration (replaces Make.com webhook)."""
    try:
        # Import Facebook service
        from app.services.facebook_service import facebook_service
        from app.services.groq_service import groq_service
        
        # Find the Facebook account/page
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook",
            SocialAccount.platform_user_id == request.page_id
        ).first()
        
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Facebook page not found"
            )

        if not account.access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Facebook access token not found. Please reconnect your account."
            )
        
        # Validate and potentially refresh the access token
        logger.info(f"Validating Facebook token for account {account.id}")
        validation_result = await facebook_service.validate_and_refresh_token(
            account.access_token, 
            account.token_expires_at
        )
        
        if not validation_result["valid"]:
            if validation_result.get("expired") or validation_result.get("needs_reconnection"):
                # Mark account as disconnected
                account.is_connected = False
                db.commit()
                
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Facebook login session expired. Please reconnect your account."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Facebook token validation failed: {validation_result.get('error', 'Unknown error')}"
                )
        
        # Update last sync time since token is valid
        account.last_sync_at = datetime.utcnow()
        db.commit()
        
        final_content = request.message
        ai_generated = False
        
        # Handle AI-generated content for auto posts
        if request.post_type == "auto-generated" and groq_service.is_available():
            try:
                ai_result = await groq_service.generate_facebook_post(request.message)
                if ai_result["success"]:
                    final_content = ai_result["content"]
                    ai_generated = True
            except Exception as ai_error:
                logger.error(f"AI generation failed: {ai_error}")
                # Fall back to original message if AI fails
                print(f"AI generation failed, using original message: {ai_error}")
        
        # Create post record in database
        post = Post(
            user_id=current_user.id,
            social_account_id=account.id,
            content=final_content,
            post_type=PostType.IMAGE if request.image else PostType.TEXT,
            media_urls=[request.image] if request.image else None,
            status=PostStatus.SCHEDULED,
            is_auto_post=ai_generated,
            metadata={
                "ai_generated": ai_generated,
                "original_prompt": request.message if ai_generated else None,
                "post_type": request.post_type
            }
        )
        
        db.add(post)
        db.commit()
        db.refresh(post)
        
        # Actually post to Facebook
        try:
            # Determine media type
            media_type = "text"
            if request.image:
                media_type = "photo"
            
            # Use Facebook service to create the post
            facebook_result = await facebook_service.create_post(
                page_id=request.page_id,
                access_token=account.access_token,
                message=final_content,
                media_url=request.image,
                media_type=media_type
            )
            
            # Update post status based on Facebook result
            if facebook_result and facebook_result.get("success"):
                post.status = PostStatus.PUBLISHED
                post.platform_post_id = facebook_result.get("post_id")
                post.platform_response = facebook_result
            else:
                error_msg = facebook_result.get("error", "Unknown Facebook API error")
                post.status = PostStatus.FAILED
                post.error_message = error_msg
                
                # Check if the error is due to token expiration
                if "expired" in error_msg.lower() or "session" in error_msg.lower() or "token" in error_msg.lower():
                    account.is_connected = False
                    db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Facebook login session expired. Please reconnect your account."
                    )
            
            db.commit()
            
        except HTTPException:
            raise
        except Exception as fb_error:
            logger.error(f"Facebook posting error: {fb_error}")
            post.status = PostStatus.FAILED
            post.error_message = str(fb_error)
            db.commit()
            
            # Check if the error suggests token expiration
            error_str = str(fb_error).lower()
            if "expired" in error_str or "session" in error_str or "unauthorized" in error_str:
                account.is_connected = False
                db.commit()
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Facebook login session expired. Please reconnect your account."
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to post to Facebook: {str(fb_error)}"
                )
        
        # Prepare response
        if post.status == PostStatus.PUBLISHED:
            message = "Post published successfully to Facebook!"
        elif ai_generated:
            message = "Post created with AI content (Facebook posting failed)"
        else:
            message = "Post created successfully (Facebook posting failed)"
        
        return SuccessResponse(
            message=message,
            data={
                "post_id": post.id,
                "status": post.status,
                "platform": "facebook",
                "page_name": account.display_name,
                "ai_generated": ai_generated,
                "facebook_post_id": post.platform_post_id,
                "content": final_content
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Facebook post: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to create post: {str(e)}"
        )


@router.get("/social/facebook/posts-for-auto-reply/{page_id}")
async def get_posts_for_auto_reply(
    page_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get posts from this app for auto-reply selection."""
    try:
        # Find the Facebook account/page
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook",
            SocialAccount.platform_user_id == page_id
        ).first()
        
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Facebook page not found"
            )
        
        # Get posts created by this app for this page
        posts = db.query(Post).filter(
            Post.social_account_id == account.id,
            Post.status.in_([PostStatus.PUBLISHED, PostStatus.SCHEDULED])
        ).order_by(Post.created_at.desc()).limit(50).all()
        
        # Format posts for frontend
        formatted_posts = []
        for post in posts:
            formatted_posts.append({
                "id": post.id,
                "facebook_post_id": post.platform_post_id,
                "content": post.content[:200] + "..." if len(post.content) > 200 else post.content,
                "full_content": post.content,
                "created_at": post.created_at.isoformat(),
                "status": post.status.value,
                "has_media": bool(post.media_urls),
                "media_count": len(post.media_urls) if post.media_urls else 0
            })
        
        return {
            "success": True,
            "posts": formatted_posts,
            "total_count": len(formatted_posts)
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to get posts for auto-reply: {str(e)}"
        )


@router.post("/social/facebook/auto-reply")
async def toggle_auto_reply(
    request: AutoReplyToggleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Toggle auto-reply for Facebook page with AI integration and post selection."""
    try:
        # Import Facebook service
        from app.services.facebook_service import facebook_service
        
        # Find the Facebook account/page
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook",
            SocialAccount.platform_user_id == request.page_id
        ).first()
        
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Facebook page not found"
            )
        
        # Validate selected posts if any
        selected_posts = []
        if request.selected_post_ids:
            posts = db.query(Post).filter(
                Post.id.in_(request.selected_post_ids),
                Post.social_account_id == account.id
            ).all()
            # Get the Facebook post IDs (platform_post_id) for the selected posts
            selected_posts = [post.platform_post_id for post in posts if post.platform_post_id]
            
            logger.info(f"Selected posts: {request.selected_post_ids}")
            logger.info(f"Facebook post IDs: {selected_posts}")
            logger.info(f"Found posts in DB: {[post.id for post in posts]}")
            logger.info(f"Platform post IDs: {[post.platform_post_id for post in posts]}")
        else:
            logger.info("No selected post IDs in request")
        
        # Use Facebook service to setup auto-reply
        facebook_result = await facebook_service.setup_auto_reply(
            page_id=request.page_id,
            access_token=account.access_token,
            enabled=request.enabled,
            template=request.response_template
        )
        
        # Find or create auto-reply rule in database
        auto_reply_rule = db.query(AutomationRule).filter(
            AutomationRule.user_id == current_user.id,
            AutomationRule.social_account_id == account.id,
            AutomationRule.rule_type == RuleType.AUTO_REPLY
        ).first()
        
        if auto_reply_rule:
            # Update existing rule
            auto_reply_rule.is_active = request.enabled
            rule_actions = {
                "response_template": request.response_template,
                "ai_enabled": True,
                "facebook_setup": facebook_result,
                "selected_post_ids": request.selected_post_ids,
                "selected_facebook_post_ids": selected_posts
            }
            auto_reply_rule.actions = rule_actions
            logger.info(f"🔄 Updated existing rule {auto_reply_rule.id} with actions: {rule_actions}")
        else:
            # Create new auto-reply rule
            rule_actions = {
                "response_template": request.response_template,
                "ai_enabled": True,
                "facebook_setup": facebook_result,
                "selected_post_ids": request.selected_post_ids,
                "selected_facebook_post_ids": selected_posts
            }
            auto_reply_rule = AutomationRule(
                user_id=current_user.id,
                social_account_id=account.id,
                name=f"Auto Reply - {account.display_name}",
                rule_type=RuleType.AUTO_REPLY,
                trigger_type=TriggerType.ENGAGEMENT_BASED,
                trigger_conditions={
                    "event": "comment",
                    "selected_posts": selected_posts
                },
                actions=rule_actions,
                is_active=request.enabled
            )
            db.add(auto_reply_rule)
            logger.info(f"🆕 Created new rule with actions: {rule_actions}")
        
        db.commit()
        logger.info(f"💾 Committed rule to database. Rule ID: {auto_reply_rule.id}")
        logger.info(f"💾 Final rule actions: {auto_reply_rule.actions}")
        
        return SuccessResponse(
            message=f"Auto-reply {'enabled' if request.enabled else 'disabled'} successfully with AI integration",
            data={
                "rule_id": auto_reply_rule.id,
                "enabled": request.enabled,
                "ai_enabled": True,
                "page_name": account.display_name,
                "facebook_setup": facebook_result,
                "selected_posts_count": len(selected_posts),
                "selected_post_ids": request.selected_post_ids
            }
        )
        
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to toggle auto-reply: {str(e)}"
        )


@router.post("/social/facebook/refresh-tokens")
async def refresh_facebook_tokens(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Validate and refresh Facebook tokens for all connected accounts."""
    try:
        from app.services.facebook_service import facebook_service
        
        # Get all Facebook accounts for this user
        facebook_accounts = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook",
            SocialAccount.is_connected == True
        ).all()
        
        if not facebook_accounts:
            return {
                "success": True,
                "message": "No Facebook accounts to refresh",
                "accounts": []
            }
        
        refresh_results = []
        
        for account in facebook_accounts:
            try:
                logger.info(f"Validating token for account {account.id} ({account.display_name})")
                
                validation_result = await facebook_service.validate_and_refresh_token(
                    account.access_token,
                    account.token_expires_at
                )
                
                if validation_result["valid"]:
                    # Token is still valid
                    account.last_sync_at = datetime.utcnow()
                    refresh_results.append({
                        "account_id": account.id,
                        "platform_user_id": account.platform_user_id,
                        "name": account.display_name,
                        "status": "valid",
                        "message": "Token is valid"
                    })
                else:
                    # Token is invalid or expired
                    if validation_result.get("expired") or validation_result.get("needs_reconnection"):
                        account.is_connected = False
                        refresh_results.append({
                            "account_id": account.id,
                            "platform_user_id": account.platform_user_id,
                            "name": account.display_name,
                            "status": "expired",
                            "message": "Token expired - reconnection required",
                            "needs_reconnection": True
                        })
                    else:
                        refresh_results.append({
                            "account_id": account.id,
                            "platform_user_id": account.platform_user_id,
                            "name": account.display_name,
                            "status": "error",
                            "message": validation_result.get("error", "Unknown validation error")
                        })
                
            except Exception as e:
                logger.error(f"Error validating account {account.id}: {e}")
                refresh_results.append({
                    "account_id": account.id,
                    "platform_user_id": account.platform_user_id,
                    "name": account.display_name,
                    "status": "error",
                    "message": f"Validation error: {str(e)}"
                })
        
        db.commit()
        
        # Count results
        valid_count = len([r for r in refresh_results if r["status"] == "valid"])
        expired_count = len([r for r in refresh_results if r["status"] == "expired"])
        error_count = len([r for r in refresh_results if r["status"] == "error"])
        
        return {
            "success": True,
            "message": f"Token validation complete: {valid_count} valid, {expired_count} expired, {error_count} errors",
            "summary": {
                "total_accounts": len(refresh_results),
                "valid": valid_count,
                "expired": expired_count,
                "errors": error_count
            },
            "accounts": refresh_results
        }
        
    except Exception as e:
        logger.error(f"Error refreshing Facebook tokens: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh tokens: {str(e)}"
        )


# Facebook Image Generation Endpoints
@router.post("/social/facebook/generate-image")
async def generate_facebook_image(
    request: ImageGenerationRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Generate an image using Stability AI for Facebook posts.
    
    This endpoint generates an image without posting it to Facebook.
    Use this to preview images before posting.
    """
    try:
        from app.services.facebook_service import facebook_service
        
        logger.info(f"Generating image for user {current_user.id} with prompt: {request.image_prompt}")
        
        # Generate image
        result = await facebook_service.generate_image_only(
            image_prompt=request.image_prompt,
            post_type=request.post_type
        )
        
        if result["success"]:
            logger.info(f"Image generated successfully for user {current_user.id}")
            return {
                "success": True,
                "message": "Image generated successfully",
                "data": {
                    "image_url": result["image_url"],
                    "filename": result["filename"],
                    "prompt": result["prompt"],
                    "image_details": result["image_details"]
                }
            }
        else:
            logger.error(f"Image generation failed for user {current_user.id}: {result.get('error')}")
            raise HTTPException(
                status_code=400,
                detail=f"Image generation failed: {result.get('error')}"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in image generation endpoint: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate image: {str(e)}"
        )


@router.post("/social/facebook/create-post")
async def create_unified_facebook_post(
    request: UnifiedFacebookPostRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """
    Simplified endpoint for creating Facebook posts with enhanced error logging.
    """
    try:
        from app.services.facebook_service import facebook_service
        
        logger.info(f"=== FACEBOOK POST DEBUG START ===")
        logger.info(f"User ID: {current_user.id}")
        logger.info(f"Request data: {request.dict()}")
        
        # Verify the user has access to the specified page
        page_account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook",
            SocialAccount.platform_user_id == request.page_id,
            SocialAccount.is_connected == True
        ).first()
        
        if not page_account:
            logger.error(f"Page not found or not connected for user {current_user.id}, page_id: {request.page_id}")
            raise HTTPException(
                status_code=404,
                detail="Facebook page not found or not connected"
            )
        
        logger.info(f"Found page account: {page_account.display_name} (ID: {page_account.id})")
        logger.info(f"Page access token length: {len(page_account.access_token) if page_account.access_token else 0}")
        
        # Determine content
        final_text_content = None
        final_image_url = None
        
        # Handle text content
        if request.use_ai_text or request.content_prompt:
            logger.info("Generating AI text content")
            from app.services.groq_service import groq_service
            text_result = await groq_service.generate_facebook_post(
                request.content_prompt or request.text_content or "Create an engaging Facebook post"
            )
            
            if not text_result["success"]:
                logger.error(f"AI text generation failed: {text_result.get('error')}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Text generation failed: {text_result.get('error', 'Unknown error')}"
                )
            
            final_text_content = text_result["content"]
            logger.info(f"Generated text content: {final_text_content[:100]}...")
        else:
            final_text_content = request.text_content
            logger.info(f"Using provided text content: {final_text_content[:100] if final_text_content else 'None'}...")
        
        # Handle image content
        final_image_url = None
        if request.use_ai_image or request.image_prompt:
            logger.info("Generating AI image content")
            image_result = await facebook_service.generate_image_only(
                image_prompt=request.image_prompt or request.content_prompt or request.text_content,
                post_type=request.post_type
            )
            
            if not image_result["success"]:
                logger.error(f"AI image generation failed: {image_result.get('error')}")
                raise HTTPException(
                    status_code=400,
                    detail=f"Image generation failed: {image_result.get('error', 'Unknown error')}"
                )
            
            final_image_url = image_result["image_url"]
            logger.info(f"Generated image URL: {final_image_url[:100] if final_image_url else 'None'}...")
        elif request.image_url:
            final_image_url = request.image_url
            logger.info(f"Using provided image URL: {final_image_url[:100] if final_image_url else 'None'}...")
        
        # Handle video content
        final_video_url = None
        if request.video_url:
            final_video_url = request.video_url
            logger.info(f"Using provided video URL: {final_video_url[:100] if final_video_url else 'None'}...")
        
        # Determine post type
        if final_video_url:
            post_type = "video"
        elif final_image_url:
            post_type = "photo"
        else:
            post_type = "text"
        logger.info(f"Post type determined: {post_type}")
        logger.info(f"Final text content: {final_text_content}")
        logger.info(f"Final image URL: {final_image_url}")
        logger.info(f"Final video URL: {final_video_url}")
        
        # Create the Facebook post using the service directly
        logger.info(f"Calling Facebook service create_post method")
        logger.info(f"Parameters: page_id={request.page_id}, message={final_text_content[:50] if final_text_content else 'None'}..., media_url={final_image_url[:50] if final_image_url else 'None'}..., media_type={post_type}")
        
        # Determine which media URL to use
        media_url = final_video_url if final_video_url else final_image_url
        
        result = await facebook_service.create_post(
            page_id=request.page_id,
            access_token=page_account.access_token,
            message=final_text_content or "Generated with AI",
            media_url=media_url,
            media_type=post_type
        )
        
        logger.info(f"Facebook service result: {result}")
        
        if result["success"]:
            # Save post to database
            post = None  # Initialize post variable
            try:
                logger.info("Saving post to database...")
                # Determine post type for database
                db_post_type = PostType.TEXT
                media_urls = []
                
                if final_video_url:
                    db_post_type = PostType.VIDEO
                    media_urls = [final_video_url]
                elif final_image_url:
                    db_post_type = PostType.IMAGE
                    media_urls = [final_image_url]
                
                post = Post(
                    user_id=current_user.id,
                    social_account_id=page_account.id,
                    post_type=db_post_type,
                    content=final_text_content or "Media post",  # Ensure content is never None
                    platform_post_id=result["post_id"],
                    status=PostStatus.PUBLISHED,
                    published_at=datetime.utcnow(),
                    media_urls=media_urls if media_urls else None,
                    platform_response={
                        "facebook_result": result,
                        "metadata": {
                            "post_type": request.post_type,
                            "ai_generated_text": request.use_ai_text or bool(request.content_prompt),
                            "ai_generated_image": request.use_ai_image or bool(request.image_prompt)
                        }
                    }
                )
                logger.info(f"Post object created: {post}")
                db.add(post)
                db.commit()
                logger.info(f"Post saved to database with ID: {post.id}")
            except Exception as db_error:
                logger.error(f"Database error while saving post: {db_error}")
                logger.error(f"Post data: user_id={current_user.id}, social_account_id={page_account.id}, content={final_text_content or 'Image post'}")
                import traceback
                logger.error(f"Database error traceback: {traceback.format_exc()}")
                # Don't fail the whole request if database save fails
                logger.warning("Continuing without database save due to error")
            
            logger.info(f"=== FACEBOOK POST SUCCESS ===")
            
            return {
                "success": True,
                "message": "Facebook post created successfully",
                "data": {
                    "post_id": result["post_id"],
                    "text_content": final_text_content,
                    "image_url": final_image_url,
                    "video_url": final_video_url,
                    "database_id": post.id if post else None  # Handle case where post wasn't saved
                }
            }
        else:
            # Enhanced error logging
            error_msg = result.get('error', 'Unknown error')
            logger.error(f"=== FACEBOOK POST FAILED ===")
            logger.error(f"Error message: {error_msg}")
            logger.error(f"Full result: {result}")
            logger.error(f"Page ID: {request.page_id}")
            logger.error(f"Access token valid: {bool(page_account.access_token)}")
            logger.error(f"Image URL type: {type(final_image_url)}")
            logger.error(f"Image URL preview: {final_image_url[:100] if final_image_url else 'None'}...")
            
            # Provide specific error messages for common issues
            if "PHOTO" in error_msg:
                detailed_error = (
                    "Facebook photo upload failed. This could be due to:\n"
                    "1. Image format not supported (use JPG, PNG, GIF)\n"
                    "2. Image file too large (max 4MB)\n"
                    "3. Page doesn't have photo posting permissions\n"
                    "4. Access token doesn't have required permissions\n"
                    "5. Facebook API rate limiting\n\n"
                    f"Technical error: {error_msg}"
                )
                raise HTTPException(status_code=400, detail=detailed_error)
            elif "permission" in error_msg.lower():
                raise HTTPException(
                    status_code=400,
                    detail=f"Permission error: {error_msg}. Please check your Facebook page permissions."
                )
            elif "token" in error_msg.lower() or "expired" in error_msg.lower():
                # Mark account as disconnected
                page_account.is_connected = False
                db.commit()
                raise HTTPException(
                    status_code=401,
                    detail="Facebook access token expired. Please reconnect your account."
                )
            else:
                raise HTTPException(
                    status_code=400,
                    detail=f"Failed to create post: {error_msg}"
                )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"=== UNEXPECTED ERROR ===")
        logger.error(f"Error creating Facebook post: {e}")
        logger.error(f"Exception type: {type(e)}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@router.post("/social/facebook/generate-caption-with-strategy")
async def generate_facebook_caption_with_custom_strategy(
    request: FacebookCustomStrategyCaptionRequest,
    current_user: User = Depends(get_current_user)
):
    """Generate Facebook caption using a custom strategy template."""
    try:
        from app.services.groq_service import groq_service
        
        result = await groq_service.generate_facebook_caption_with_custom_strategy(
            custom_strategy=request.custom_strategy,
            context=request.context,
            max_length=request.max_length
        )
        
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Facebook caption generation failed: {result.get('error', 'Unknown error')}"
            )
        
        return {
            "success": True,
            "content": result["content"],
            "custom_strategy": request.custom_strategy,
            "context": request.context
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Facebook caption with custom strategy: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Facebook caption: {str(e)}"
        )


@router.post("/social/facebook/generate-bulk-captions")
async def generate_facebook_bulk_captions(
    request: BulkCaptionGenerationRequest,
    current_user: User = Depends(get_current_user)
):
    """Generate captions for multiple Facebook posts using a custom strategy template."""
    try:
        from app.services.groq_service import groq_service
        
        captions = []
        
        for context in request.contexts:
            result = await groq_service.generate_facebook_caption_with_custom_strategy(
                custom_strategy=request.custom_strategy,
                context=context,
                max_length=request.max_length
            )
            
            if result["success"]:
                captions.append({
                    "content": result["content"],
                    "context": context,
                    "success": True
                })
            else:
                captions.append({
                    "content": f"Failed to generate caption for: {context}",
                    "context": context,
                    "success": False,
                    "error": result.get("error", "Unknown error")
                })
        
        return {
            "success": True,
            "captions": captions,
            "custom_strategy": request.custom_strategy,
            "total_generated": len([c for c in captions if c["success"]])
        }
        
    except Exception as e:
        logger.error(f"Error generating Facebook bulk captions: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Facebook bulk captions: {str(e)}"
        )


# Instagram Integration
@router.post("/social/instagram/connect")
async def connect_instagram(
    request: InstagramConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Connect Instagram Business account through Facebook."""
    try:
        logger.info(f"Instagram connect request for user {current_user.id}")
        
        # Use the new service to get Instagram accounts with proper error handling
        try:
            instagram_accounts = instagram_service.get_facebook_pages_with_instagram(request.access_token)
        except Exception as service_error:
            # The service provides detailed troubleshooting messages
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(service_error)
            )
        
        # Save Instagram accounts to database
        connected_accounts = []
        for ig_account in instagram_accounts:
            # Check if account already exists for any user
            existing_account_any_user = db.query(SocialAccount).filter(
                SocialAccount.platform == "instagram",
                SocialAccount.platform_user_id == ig_account["platform_id"]
            ).first()
            if existing_account_any_user and existing_account_any_user.user_id != current_user.id:
                # Prevent connecting the same Instagram account to a different user
                raise HTTPException(
                    status_code=400,
                    detail=f"Instagram account @{ig_account['username']} is already connected to another user."
                )
            # Check if account already exists for this user
            existing_account = db.query(SocialAccount).filter(
                SocialAccount.user_id == current_user.id,
                SocialAccount.platform == "instagram",
                SocialAccount.platform_user_id == ig_account["platform_id"]
            ).first()
            if existing_account:
                # Update existing account
                existing_account.username = ig_account["username"]
                existing_account.display_name = ig_account["display_name"] or ig_account["username"]
                existing_account.is_connected = True
                existing_account.last_sync_at = datetime.utcnow()
                existing_account.follower_count = ig_account.get("followers_count", 0)
                existing_account.profile_picture_url = ig_account.get("profile_picture")
                existing_account.platform_data = {
                    "page_id": ig_account.get("page_id"),
                    "page_name": ig_account.get("page_name"),
                    "media_count": ig_account.get("media_count", 0),
                    "page_access_token": ig_account.get("page_access_token")
                }
                existing_account.access_token = ig_account.get("page_access_token")
                db.commit()
                connected_accounts.append(existing_account)
                logger.info(f"Updated existing Instagram account: {ig_account['username']} (ID: {ig_account['platform_id']})")
            else:
                # Create new account  
                ig_account_obj = SocialAccount(
                    user_id=current_user.id,
                    platform="instagram",
                    platform_user_id=ig_account["platform_id"],
                    username=ig_account["username"],
                    display_name=ig_account["display_name"] or ig_account["username"],
                    account_type="business",
                    follower_count=ig_account.get("followers_count", 0),
                    profile_picture_url=ig_account.get("profile_picture"),
                    platform_data={
                        "page_id": ig_account.get("page_id"),
                        "page_name": ig_account.get("page_name"),
                        "media_count": ig_account.get("media_count", 0),
                        "page_access_token": ig_account.get("page_access_token")
                    },
                    access_token=ig_account.get("page_access_token"),
                    is_connected=True,
                    last_sync_at=datetime.utcnow()
                )
                db.add(ig_account_obj)
                db.commit()
                db.refresh(ig_account_obj)
                connected_accounts.append(ig_account_obj)
                logger.info(f"Created new Instagram account: {ig_account['username']} (ID: {ig_account['platform_id']})")
        
        logger.info(f"Instagram connection successful. Connected accounts: {len(connected_accounts)}")
        
        return SuccessResponse(
            message=f"Instagram account(s) connected successfully ({len(connected_accounts)} accounts)",
            data={
                "accounts": [{
                    "platform_id": acc.platform_user_id,
                    "username": acc.username,
                    "display_name": acc.display_name,
                    "page_name": acc.platform_data.get("page_name"),
                    "followers_count": acc.follower_count or 0,
                    "media_count": acc.platform_data.get("media_count", 0),
                    "profile_picture": acc.profile_picture_url
                } for acc in connected_accounts]
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting Instagram account: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect Instagram account: {str(e)}"
        )


@router.post("/social/instagram/post")
async def create_instagram_post(
    request: InstagramPostRequest = None,
    instagram_user_id: str = None,
    caption: str = None,
    image_url: str = None,
    post_type: str = "manual",
    use_ai: bool = False,
    prompt: str = None,
    image: UploadFile = File(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create and publish an Instagram post."""
    try:
        # Handle both JSON and FormData requests
        if request:
            # JSON request
            instagram_user_id = request.instagram_user_id
            caption = request.caption
            image_url = request.image_url
            post_type = request.post_type
            use_ai = getattr(request, 'use_ai', False)
            prompt = getattr(request, 'prompt', None)
        else:
            # FormData request - parameters are already available
            pass
        
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instagram account not found"
            )
        
        # Get the page access token from platform_data
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Page access token not found. Please reconnect your Instagram account."
            )
        
        # Handle file upload if present
        final_image_url = image_url
        if image and image.filename:
            # TODO: Implement file upload to cloud storage (AWS S3, etc.)
            # For now, we'll return an error for file uploads
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File upload not yet implemented. Please use image URL instead."
            )
        
        # Create the post using Instagram service
        if post_type == "post-auto" or use_ai:
            # AI-generated post
            post_result = await instagram_service.create_ai_generated_post(
                instagram_user_id=instagram_user_id,
                access_token=page_access_token,
                prompt=prompt or caption,
                image_url=final_image_url
            )
        else:
            # Manual post
            try:
                post_result = instagram_service.create_post(
                    instagram_user_id=instagram_user_id,
                    page_access_token=page_access_token,
                    caption=caption,
                    image_url=final_image_url
                )
            except Exception as service_error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(service_error)
                )
        
        if not post_result["success"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to create Instagram post: {post_result.get('error', 'Unknown error')}"
            )
        
        # Save post to database
        post = Post(
            user_id=current_user.id,
            social_account_id=account.id,
            content=post_result.get("generated_caption") or caption,
            post_type=PostType.IMAGE,
            status=PostStatus.PUBLISHED,
            platform_post_id=post_result.get("post_id"),
            published_at=datetime.utcnow(),
            media_urls=[final_image_url] if final_image_url else None
        )
        
        db.add(post)
        db.commit()
        db.refresh(post)
        
        return SuccessResponse(
            message="Instagram post created successfully",
            data={
                "post_id": post_result.get("post_id"),
                "database_id": post.id,
                "platform": "instagram",
                "account_username": account.username,
                "ai_generated": post_result.get("ai_generated", False),
                "generated_caption": post_result.get("generated_caption"),
                "original_prompt": post_result.get("original_prompt")
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Instagram post: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create Instagram post: {str(e)}"
        )


@router.get("/social/instagram/media/{instagram_user_id}")
async def get_instagram_media(
    instagram_user_id: str,
    limit: int = 25,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get Instagram media for a connected account."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instagram account not found"
            )
        
        # Get the page access token from platform_data
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Page access token not found. Please reconnect your Instagram account."
            )
        
        # Get media from Instagram API using new service
        media_items = instagram_service.get_user_media(
            instagram_user_id=instagram_user_id,
            page_access_token=page_access_token,
            limit=limit
        )
        
        return SuccessResponse(
            message=f"Retrieved {len(media_items)} media items",
            data={
                "media": media_items,
                "account_username": account.username,
                "total_items": len(media_items)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting Instagram media: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get Instagram media: {str(e)}"
        )


@router.post("/social/instagram/generate-image")
async def generate_instagram_image(
    request: InstagramImageGenerationRequest,
    current_user: User = Depends(get_current_user)
):
    """Generate an image for Instagram using Stability AI."""
    try:
        from app.services.instagram_service import instagram_service
        from app.services.cloudinary_service import cloudinary_service
        
        logger.info(f"Generating Instagram image with prompt: {request.image_prompt}")
        
        # Generate image using Instagram-optimized Stability AI
        image_result = await instagram_service.generate_instagram_image_with_ai(
            prompt=request.image_prompt,
            post_type=request.post_type
        )
        
        if not image_result["success"]:
            raise HTTPException(
                status_code=500,
                detail=f"Image generation failed: {image_result.get('error', 'Unknown error')}"
            )
        
        # Upload to Cloudinary with Instagram-specific transforms
        upload_result = cloudinary_service.upload_image_with_instagram_transform(
            f"data:image/png;base64,{image_result['image_base64']}"
        )
        
        if not upload_result["success"]:
            raise HTTPException(
                status_code=500,
                detail=f"Image upload failed: {upload_result.get('error', 'Unknown error')}"
            )
        
        return SuccessResponse(
            message="Instagram image generated successfully",
            data={
                "image_url": upload_result["url"],
                "filename": f"instagram_generated_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.jpg",
                "prompt": request.image_prompt,
                "enhanced_prompt": image_result.get("enhanced_prompt"),
                "post_type": request.post_type,
                "width": image_result.get("width"),
                "height": image_result.get("height")
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Instagram image: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate Instagram image: {str(e)}"
        )


@router.post("/social/instagram/upload-image")
async def upload_instagram_image(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Upload an image for Instagram using Cloudinary with Instagram-specific transforms."""
    try:
        from app.services.cloudinary_service import cloudinary_service
        
        # Validate file type
        if not file.content_type.startswith('image/'):
            raise HTTPException(
                status_code=400,
                detail="Only image files are allowed"
            )
        
        # Read file content
        file_content = await file.read()
        
        # Upload to Cloudinary with Instagram-specific transforms
        upload_result = cloudinary_service.upload_image_with_instagram_transform(file_content)
        
        if not upload_result["success"]:
            raise HTTPException(
                status_code=500,
                detail=f"Image upload failed: {upload_result.get('error', 'Unknown error')}"
            )
        
        return SuccessResponse(
            message="Image uploaded successfully for Instagram",
            data={
                "url": upload_result["url"],
                "filename": file.filename,
                "size": len(file_content)
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading Instagram image: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload Instagram image: {str(e)}"
        )


@router.post("/social/instagram/upload-video")
async def upload_instagram_video(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Upload a video for Instagram - saves to disk and uploads to Cloudinary."""
    try:
        from app.services.cloudinary_service import cloudinary_service
        import os
        
        # Validate file type
        if not file.content_type.startswith('video/'):
            raise HTTPException(
                status_code=400,
                detail="Only video files are allowed"
            )
        
        # Read file content
        file_content = await file.read()
        
        # Create temp_images directory if it doesn't exist
        os.makedirs("temp_images", exist_ok=True)
        
        # Save file to temp_images directory for later use (like Facebook service)
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1], dir="temp_images") as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name
        
        # Get just the filename for database storage
        saved_filename = os.path.basename(temp_file_path)
        
        logger.info(f"Video file saved to disk: {temp_file_path}")
        logger.info(f"File size: {len(file_content)} bytes")
        logger.info(f"Saved filename: {saved_filename}")
        
        # Upload to Cloudinary with Instagram-specific transforms
        upload_result = cloudinary_service.upload_video_with_instagram_transform(file_content)
        
        if not upload_result["success"]:
            # Clean up temp file if upload failed
            try:
                os.unlink(temp_file_path)
                logger.warning(f"Cleaned up temp file after failed upload: {temp_file_path}")
            except:
                pass
            raise HTTPException(
                status_code=500,
                detail=f"Video upload failed: {upload_result.get('error', 'Unknown error')}"
            )
        
        logger.info(f"Video uploaded to Cloudinary successfully: {upload_result['url']}")
        
        return SuccessResponse(
            message="Video uploaded successfully for Instagram",
            data={
                "url": upload_result["url"],  # Cloudinary URL for immediate use
                "filename": saved_filename,   # Saved filename for later file-based posting
                "original_filename": file.filename,
                "size": len(file_content),
                "cloudinary_url": upload_result["url"],
                "file_path": temp_file_path  # Full path for backend use
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading Instagram video: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload Instagram video: {str(e)}"
        )


@router.post("/social/instagram/upload-thumbnail")
async def upload_instagram_thumbnail(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user)
):
    """Upload a thumbnail image for Instagram reels using Cloudinary with Instagram-specific transforms."""
    try:
        from app.services.cloudinary_service import cloudinary_service
        import os
        
        # Validate file type
        if not file.content_type.startswith('image/'):
            raise HTTPException(
                status_code=400,
                detail="Only image files are allowed for thumbnails"
            )
        
        # Read file content
        file_content = await file.read()
        
        # Create temp_images directory if it doesn't exist
        os.makedirs("temp_images", exist_ok=True)
        
        # Save file to temp_images directory for later use
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file.filename)[1], dir="temp_images") as temp_file:
            temp_file.write(file_content)
            temp_file_path = temp_file.name
        
        # Get just the filename for database storage
        saved_filename = os.path.basename(temp_file_path)
        
        logger.info(f"Thumbnail file saved to disk: {temp_file_path}")
        logger.info(f"File size: {len(file_content)} bytes")
        logger.info(f"Saved filename: {saved_filename}")
        
        # Upload to Cloudinary with Instagram-specific transforms for thumbnails
        upload_result = cloudinary_service.upload_thumbnail_with_instagram_transform(file_content)
        
        if not upload_result["success"]:
            # Clean up temp file if upload failed
            try:
                os.unlink(temp_file_path)
                logger.warning(f"Cleaned up temp file after failed upload: {temp_file_path}")
            except:
                pass
            raise HTTPException(
                status_code=500,
                detail=f"Thumbnail upload failed: {upload_result.get('error', 'Unknown error')}"
            )
        
        logger.info(f"Thumbnail uploaded to Cloudinary successfully: {upload_result['url']}")
        
        return SuccessResponse(
            message="Thumbnail uploaded successfully for Instagram reels",
            data={
                "url": upload_result["url"],  # Cloudinary URL for immediate use
                "filename": saved_filename,   # Saved filename for later file-based posting
                "original_filename": file.filename,
                "size": len(file_content),
                "cloudinary_url": upload_result["url"],
                "file_path": temp_file_path  # Full path for backend use
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading Instagram thumbnail: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload Instagram thumbnail: {str(e)}"
        )


@router.post("/social/instagram/generate-caption")
async def generate_instagram_caption(
    request: dict,
    current_user: User = Depends(get_current_user)
):
    """Generate Instagram caption using AI."""
    try:
        from app.services.groq_service import groq_service
        
        prompt = request.get("prompt", "")
        if not prompt:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Prompt is required for caption generation"
            )
        
        result = await groq_service.generate_instagram_post(prompt)
        
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Caption generation failed: {result.get('error', 'Unknown error')}"
            )
        
        return {
            "success": True,
            "content": result["content"],
            "prompt": prompt
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Instagram caption: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate caption: {str(e)}"
        )


@router.post("/social/generate-caption-with-strategy")
async def generate_caption_with_custom_strategy(
    request: CustomStrategyCaptionRequest,
    current_user: User = Depends(get_current_user)
):
    """Generate caption using a custom strategy template."""
    try:
        from app.services.groq_service import groq_service
        
        result = await groq_service.generate_caption_with_custom_strategy(
            custom_strategy=request.custom_strategy,
            context=request.context,
            max_length=request.max_length
        )
        
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Caption generation failed: {result.get('error', 'Unknown error')}"
            )
        
        return {
            "success": True,
            "content": result["content"],
            "custom_strategy": request.custom_strategy,
            "context": request.context
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating caption with custom strategy: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate caption: {str(e)}"
        )


@router.post("/social/generate-bulk-captions")
async def generate_bulk_captions(
    request: BulkCaptionGenerationRequest,
    current_user: User = Depends(get_current_user)
):
    """Generate captions for multiple posts using a custom strategy template."""
    try:
        from app.services.groq_service import groq_service
        
        captions = []
        
        for context in request.contexts:
            result = await groq_service.generate_caption_with_custom_strategy(
                custom_strategy=request.custom_strategy,
                context=context,
                max_length=request.max_length
            )
            
            if result["success"]:
                captions.append({
                    "content": result["content"],
                    "context": context,
                    "success": True
                })
            else:
                captions.append({
                    "content": f"Failed to generate caption for: {context}",
                    "context": context,
                    "success": False,
                    "error": result.get("error", "Unknown error")
                })
        
        return {
            "success": True,
            "captions": captions,
            "custom_strategy": request.custom_strategy,
            "total_generated": len([c for c in captions if c["success"]])
        }
        
    except Exception as e:
        logger.error(f"Error generating bulk captions: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate bulk captions: {str(e)}"
        )


@router.post("/social/instagram/generate-carousel")
async def generate_instagram_carousel(
    request: InstagramCarouselGenerationRequest,
    current_user: User = Depends(get_current_user)
):
    """Generate Instagram carousel images using AI."""
    try:
        result = await instagram_service.generate_carousel_images_with_ai(
            prompt=request.image_prompt,
            count=request.count,
            post_type=request.post_type
        )
        
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Carousel generation failed: {result.get('error', 'Unknown error')}"
            )
        
        return {
            "success": True,
            "image_urls": result["image_urls"],
            "caption": result["caption"],
            "count": result["count"],
            "prompt": result["prompt"],
            "width": result["width"],
            "height": result["height"],
            "post_type": result["post_type"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Instagram carousel: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate carousel: {str(e)}"
        )


@router.post("/social/instagram/post-carousel")
async def create_instagram_carousel_post(
    request: InstagramCarouselPostRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create an Instagram carousel post."""
    try:
        logger.info(f"Starting Instagram carousel post creation for user {current_user.id}")
        logger.info(f"Request data: instagram_user_id={request.instagram_user_id}, caption_length={len(request.caption)}, image_count={len(request.image_urls)}")
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == request.instagram_user_id
        ).first()
        
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instagram account not found"
            )
        
        # Get the page access token from platform_data
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Page access token not found. Please reconnect your Instagram account."
            )
        
        # Create the carousel post
        result = await instagram_service.create_carousel_post(
            instagram_user_id=request.instagram_user_id,
            page_access_token=page_access_token,
            caption=request.caption,
            image_urls=request.image_urls
        )
        
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create carousel post: {result.get('error', 'Unknown error')}"
            )
        
        # Save post to database
        post = Post(
            user_id=current_user.id,
            social_account_id=account.id,
            content=request.caption,
            post_type=PostPostType.CAROUSEL.value,  # FIXED: use the enum value, not the enum object
            status=PostStatus.PUBLISHED,
            platform_post_id=result.get("post_id"),
            published_at=datetime.utcnow(),
            media_urls=request.image_urls
        )
        
        db.add(post)
        db.commit()
        db.refresh(post)
        
        return SuccessResponse(
            message="Instagram carousel post created successfully",
            data={
                "post_id": result.get("post_id"),
                "database_id": post.id,
                "platform": "instagram",
                "account_username": account.username,
                "caption": request.caption,
                "image_count": len(request.image_urls),
                "media_type": "carousel"
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Instagram carousel post: {str(e)}", exc_info=True)
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create carousel post: {str(e)}"
        )


@router.post("/social/instagram/create-post")
async def create_unified_instagram_post(
    request: UnifiedInstagramPostRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    print("=== API: /instagram/create-post endpoint called ===")
    print("Incoming Instagram post request:", request.dict())
    """Create an Instagram post with unified options (AI generation, file upload, etc.)."""
    try:
        # Debug logging
        logger.info(f"Received Instagram post request: {request}")
        logger.info(f"Request data: instagram_user_id={request.instagram_user_id}, "
                   f"caption={request.caption}, image_url={request.image_url}, "
                   f"video_url={request.video_url}, video_filename={request.video_filename}, "
                   f"media_type={request.media_type}")
        logger.info(f"Post type determination: media_type='{request.media_type}', "
                   f"has_video_url={bool(request.video_url)}, has_image_url={bool(request.image_url)}")
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == request.instagram_user_id
        ).first()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instagram account not found"
            )
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Page access token not found. Please reconnect your Instagram account."
            )
        # Determine post type
        is_reel = (request.media_type == "video" or request.post_type == "reel")
        is_carousel = (request.post_type == "carousel")
        is_photo = not is_reel and not is_carousel
        # --- Add thumbnail fields ---
        final_thumbnail_url = getattr(request, 'thumbnail_url', None)
        final_thumbnail_filename = getattr(request, 'thumbnail_filename', None)
        final_thumbnail_file_path = getattr(request, 'thumbnail_file', None)
        # --- Main logic ---
        try:
            if is_reel:
                # REEL: Store in Post only
                post_result = await instagram_service.create_post(
                    instagram_user_id=request.instagram_user_id,
                    page_access_token=page_access_token,
                    caption=request.caption,
                    video_url=request.video_url,
                    is_reel=True,
                    thumbnail_url=final_thumbnail_url,
                    thumbnail_filename=final_thumbnail_filename,
                    thumbnail_file_path=final_thumbnail_file_path
                )
                actual_thumbnail_url = post_result.get("reel_thumbnail_url") or final_thumbnail_url
                actual_thumbnail_filename = post_result.get("reel_thumbnail_filename") or final_thumbnail_filename
                logger.info(f"DEBUG: final_thumbnail_url before saving Post: {final_thumbnail_url}")
                new_post = Post(
                    user_id=current_user.id,
                    social_account_id=account.id,
                    content=request.caption,
                    post_type=PostPostType.REEL.value,
                    media_urls=[request.video_url],
                    status=PostStatus.PUBLISHED,
                    platform_post_id=post_result.get("post_id"),
                    error_message=None,
                    reel_thumbnail_url=actual_thumbnail_url,
                    reel_thumbnail_filename=actual_thumbnail_filename
                )
                db.add(new_post)
                db.commit()
                db.refresh(new_post)
                return {"success": True, "post_id": new_post.id, "reel_thumbnail_url": actual_thumbnail_url, "reel_thumbnail_filename": actual_thumbnail_filename}
            elif is_carousel:
                # CAROUSEL: Store in Post only (should use /post-carousel endpoint, but handle here for safety)
                post_result = await instagram_service.create_carousel_post(
                    instagram_user_id=request.instagram_user_id,
                    page_access_token=page_access_token,
                    caption=request.caption,
                    image_urls=request.image_urls
                )
                new_post = Post(
                    user_id=current_user.id,
                    social_account_id=account.id,
                    content=request.caption,
                    post_type=PostPostType.CAROUSEL.value,
                    media_urls=request.image_urls,
                    status=PostStatus.PUBLISHED,
                    platform_post_id=post_result.get("post_id"),
                    error_message=None
                )
                db.add(new_post)
                db.commit()
                db.refresh(new_post)
                return {"success": True, "post_id": new_post.id}
            else:
                # PHOTO: Store in SingleInstagramPost only
                post_result = await instagram_service.create_post(
                    instagram_user_id=request.instagram_user_id,
                    page_access_token=page_access_token,
                    caption=request.caption,
                    image_url=request.image_url,
                    is_reel=False
                )
                new_single_post = SingleInstagramPost(
                    user_id=current_user.id,
                    social_account_id=account.id,
                    post_type="photo",
                    media_url=[request.image_url],
                    caption=request.caption,
                    use_ai_image=request.use_ai_image,
                    use_ai_text=request.use_ai_text,
                    platform_post_id=post_result.get("post_id"),
                    status="published",
                    published_at=datetime.utcnow()
                )
                db.add(new_single_post)
                db.commit()
                db.refresh(new_single_post)
                return {"success": True, "post_id": new_single_post.id}
        except Exception as service_error:
            logger.error(f"Error posting to Instagram: {service_error}")
            if is_reel or is_carousel:
                failed_post = Post(
                    user_id=current_user.id,
                    social_account_id=account.id,
                    content=request.caption,
                    post_type=PostPostType.REEL.value if is_reel else PostPostType.CAROUSEL.value,
                    media_urls=[request.video_url] if is_reel else request.image_urls,
                    status=PostStatus.FAILED,
                    platform_post_id=None,
                    error_message=str(service_error),
                    reel_thumbnail_url=final_thumbnail_url if is_reel else None,
                    reel_thumbnail_filename=final_thumbnail_filename if is_reel else None
                )
                db.add(failed_post)
                db.commit()
                db.refresh(failed_post)
                return {"success": False, "error": str(service_error)}
            else:
                failed_single_post = SingleInstagramPost(
                    user_id=current_user.id,
                    social_account_id=account.id,
                    post_type="photo",
                    media_url=[request.image_url],
                    caption=request.caption,
                    use_ai_image=request.use_ai_image,
                    use_ai_text=request.use_ai_text,
                    platform_post_id=None,
                    status="failed",
                    error_message=str(service_error)
                )
                db.add(failed_single_post)
                db.commit()
                db.refresh(failed_single_post)
                return {"success": False, "error": str(service_error)}
    except Exception as e:
        logger.error(f"Error creating unified Instagram post: {str(e)}", exc_info=True)
        return {"success": False, "error": str(e)}


# Post Management
@router.get("/social/posts", response_model=List[PostResponse])
async def get_posts(
    platform: Optional[str] = None,
    status: Optional[PostStatus] = None,
    limit: int = 50,
    social_account_id: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's posts with optional filtering."""
    query = db.query(Post).filter(Post.user_id == current_user.id)
    
    if platform:
        query = query.join(SocialAccount).filter(SocialAccount.platform == platform)
    
    if status:
        query = query.filter(Post.status == status)
    
    if social_account_id:
        query = query.filter(Post.social_account_id == social_account_id)
    
    posts = query.order_by(Post.created_at.desc()).limit(limit).all()
    return posts


@router.post("/social/posts", response_model=PostResponse)
async def create_post(
    post_data: PostCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new social media post."""
    # Verify user owns the social account
    account = db.query(SocialAccount).filter(
        SocialAccount.id == post_data.social_account_id,
        SocialAccount.user_id == current_user.id
    ).first()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Social account not found"
        )
    
    post = Post(
        user_id=current_user.id,
        social_account_id=post_data.social_account_id,
        content=post_data.content,
        post_type=post_data.post_type,
        link_url=post_data.link_url,
        hashtags=post_data.hashtags,
        media_urls=post_data.media_urls,
        scheduled_at=post_data.scheduled_at,
        status=PostStatus.SCHEDULED if post_data.scheduled_at else PostStatus.DRAFT
    )
    
    db.add(post)
    db.commit()
    db.refresh(post)
    
    return post


@router.put("/social/posts/{post_id}", response_model=PostResponse)
async def update_post(
    post_id: int,
    post_data: PostUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a post."""
    post = db.query(Post).filter(
        Post.id == post_id,
        Post.user_id == current_user.id
    ).first()
    
    if not post:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Post not found"
        )
    
    # Update fields
    if post_data.content is not None:
        post.content = post_data.content
    if post_data.scheduled_at is not None:
        post.scheduled_at = post_data.scheduled_at
    if post_data.status is not None:
        post.status = post_data.status
    
    db.commit()
    db.refresh(post)
    
    return post


# Automation Rules Management
@router.get("/social/automation-rules", response_model=List[AutomationRuleResponse])
async def get_automation_rules(
    platform: Optional[str] = None,
    rule_type: Optional[str] = Query(None),  # Accept as string
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user's automation rules."""
    query = db.query(AutomationRule).filter(AutomationRule.user_id == current_user.id)
    
    if platform:
        query = query.join(SocialAccount).filter(SocialAccount.platform == platform)
    
    if rule_type:
        # Convert to enum if needed
        try:
            rule_type_enum = RuleType(rule_type)
        except ValueError:
            # fallback: try lowercase
            try:
                rule_type_enum = RuleType(rule_type.lower())
            except Exception:
                raise HTTPException(status_code=400, detail=f"Invalid rule_type: {rule_type}")
        query = query.filter(AutomationRule.rule_type == rule_type_enum)
    
    rules = query.order_by(AutomationRule.created_at.desc()).all()
    return rules


@router.post("/social/automation-rules", response_model=AutomationRuleResponse)
async def create_automation_rule(
    rule_data: AutomationRuleCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create a new automation rule."""
    # Verify user owns the social account
    account = db.query(SocialAccount).filter(
        SocialAccount.id == rule_data.social_account_id,
        SocialAccount.user_id == current_user.id
    ).first()
    
    if not account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Social account not found"
        )
    
    rule = AutomationRule(
        user_id=current_user.id,
        social_account_id=rule_data.social_account_id,
        name=rule_data.name,
        description=rule_data.description,
        rule_type=rule_data.rule_type,
        trigger_type=rule_data.trigger_type,
        trigger_conditions=rule_data.trigger_conditions,
        actions=rule_data.actions,
        daily_limit=rule_data.daily_limit,
        active_hours_start=rule_data.active_hours_start,
        active_hours_end=rule_data.active_hours_end,
        active_days=rule_data.active_days
    )
    
    db.add(rule)
    db.commit()
    db.refresh(rule)
    
    return rule


@router.put("/social/automation-rules/{rule_id}", response_model=AutomationRuleResponse)
async def update_automation_rule(
    rule_id: int,
    rule_data: AutomationRuleUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update an automation rule."""
    rule = db.query(AutomationRule).filter(
        AutomationRule.id == rule_id,
        AutomationRule.user_id == current_user.id
    ).first()
    
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found"
        )
    
    # Update fields
    if rule_data.name is not None:
        rule.name = rule_data.name
    if rule_data.description is not None:
        rule.description = rule_data.description
    if rule_data.trigger_conditions is not None:
        rule.trigger_conditions = rule_data.trigger_conditions
    if rule_data.actions is not None:
        rule.actions = rule_data.actions
    if rule_data.is_active is not None:
        rule.is_active = rule_data.is_active
    if rule_data.daily_limit is not None:
        rule.daily_limit = rule_data.daily_limit
    
    db.commit()
    db.refresh(rule)
    
    return rule


@router.delete("/social/automation-rules/{rule_id}")
async def delete_automation_rule(
    rule_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete an automation rule."""
    rule = db.query(AutomationRule).filter(
        AutomationRule.id == rule_id,
        AutomationRule.user_id == current_user.id
    ).first()
    
    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found"
        )
    
    db.delete(rule)
    db.commit()
    
    return SuccessResponse(message="Automation rule deleted successfully")








@router.get("/social/bulk-composer/content")
async def get_bulk_composer_content(
    social_account_id: int = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all bulk composer content for the current user, optionally filtered by social account."""
    try:
        query = db.query(BulkComposerContent).filter(
            BulkComposerContent.user_id == current_user.id
        )
        if social_account_id:
            query = query.filter(BulkComposerContent.social_account_id == social_account_id)
        content = query.order_by(BulkComposerContent.scheduled_datetime.desc()).all()
        
        return {
            "success": True,
            "data": [
                {
                    "id": item.id,
                    "caption": item.caption,
                    "scheduled_date": item.scheduled_date,
                    "scheduled_time": item.scheduled_time,
                    "status": item.status,
                    "has_media": bool(item.media_file),
                    "media_file": item.media_file,
                    "media_filename": item.media_filename,
                    "facebook_post_id": item.facebook_post_id,
                    "error_message": item.error_message,
                    "created_at": item.created_at.isoformat() if item.created_at else None,
                    "schedule_batch_id": item.schedule_batch_id
                }
                for item in content
            ]
        }
    
    except Exception as e:
        logger.error(f"Error getting bulk composer content: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to get bulk composer content"
        )


@router.delete("/social/bulk-composer/content/{content_id}")
async def delete_bulk_composer_content(
    content_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a bulk composer content item."""
    try:
        content = db.query(BulkComposerContent).filter(
            BulkComposerContent.id == content_id,
            BulkComposerContent.user_id == current_user.id
        ).first()
        
        if not content:
            raise HTTPException(
                status_code=404,
                detail="Content not found"
            )
        
        db.delete(content)
        db.commit()
        
        return SuccessResponse(
            message="Content deleted successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting bulk composer content: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to delete content"
        )


@router.post("/social/bulk-composer/schedule")
async def schedule_bulk_composer_posts(
    request: BulkComposerRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Schedule multiple posts for the bulk composer."""
    try:
        results = []
        schedule_batch_id = str(uuid4())  # Unique batch ID for this scheduling action
        for post in request.posts:
            try:
                # Validate required fields
                if not (post.caption and post.scheduled_date and post.scheduled_time):
                    results.append({
                        "success": False, 
                        "error": "Missing required fields", 
                        "caption": post.caption
                    })
                    continue

                # Parse scheduled datetime
                try:
                    import pytz
                    ist = pytz.timezone("Asia/Kolkata")
                    # Parse as IST, then convert to UTC for storage
                    scheduled_datetime = ist.localize(
                        datetime.strptime(f"{post.scheduled_date} {post.scheduled_time}", "%Y-%m-%d %H:%M")
                    )
                    scheduled_datetime = scheduled_datetime.astimezone(pytz.utc)
                    # Validate that the scheduled time is in the future
                    now = datetime.now(pytz.utc)
                    if scheduled_datetime <= now:
                        results.append({
                            "success": False, 
                            "error": f"Scheduled time is in the past: {scheduled_datetime}", 
                            "caption": post.caption
                        })
                        continue
                except Exception as e:
                    results.append({
                        "success": False, 
                        "error": f"Invalid date/time: {e}", 
                        "caption": post.caption
                    })
                    continue

                # Handle media upload if present
                media_url = None
                if post.media_file:
                    # If it's a base64 string, upload to Cloudinary
                    if isinstance(post.media_file, str) and post.media_file.startswith("data:image"):
                        upload_result = cloudinary_service.upload_image_with_instagram_transform(post.media_file)
                        if upload_result.get("success"):
                            media_url = upload_result["url"]
                        else:
                            results.append({
                                "success": False, 
                                "error": upload_result.get("error", "Cloudinary upload failed"), 
                                "caption": post.caption
                            })
                            continue
                    elif isinstance(post.media_file, str) and post.media_file.startswith("data:video"):
                        upload_result = cloudinary_service.upload_video_with_instagram_transform(post.media_file)
                        if upload_result.get("success"):
                            media_url = upload_result["url"]
                        else:
                            results.append({
                                "success": False, 
                                "error": upload_result.get("error", "Cloudinary upload failed"), 
                                "caption": post.caption
                            })
                            continue
                    else:
                        # Assume it's already a URL
                        media_url = post.media_file

                # Save to DB
                new_post = BulkComposerContent(
                    user_id=current_user.id,
                    social_account_id=request.social_account_id,
                    caption=post.caption,
                    media_file=media_url,
                    media_filename=post.media_filename,
                    media_generated=False,  # Default to False for uploaded media
                    scheduled_date=post.scheduled_date,
                    scheduled_time=post.scheduled_time,
                    scheduled_datetime=scheduled_datetime,
                    status=BulkComposerStatus.SCHEDULED.value,
                    schedule_batch_id=schedule_batch_id  # Assign batch ID
                )
                db.add(new_post)
                db.commit()
                db.refresh(new_post)
                
                # Schedule pre-posting notification (10 minutes before)
                try:
                    from app.services.notification_service import notification_service
                    await notification_service.schedule_pre_posting_alert(db, new_post.id)
                    logger.info(f"✅ Scheduled pre-posting alert for bulk composer post {new_post.id}")
                except Exception as notif_error:
                    logger.error(f"Failed to schedule pre-posting alert for post {new_post.id}: {notif_error}")
                try:
                    from app.services.notification_service import notification_service
                    await notification_service.schedule_pre_posting_alert(db, new_post.id)
                except Exception as notif_error:
                    logger.error(f"Failed to schedule pre-posting alert: {notif_error}")
                
                results.append({
                    "success": True, 
                    "id": new_post.id, 
                    "caption": post.caption,
                    "schedule_batch_id": schedule_batch_id
                })
                
            except Exception as e:
                logger.error(f"Error scheduling post: {e}")
                results.append({
                    "success": False, 
                    "error": str(e), 
                    "caption": post.caption
                })
        # Determine overall success
        failed_posts = [r for r in results if not r["success"]]
        scheduled_posts = [r for r in results if r["success"]]
        if failed_posts:
            return {
                "success": False,
                "message": f"Bulk scheduling completed with {len(scheduled_posts)} successes and {len(failed_posts)} failures.",
                "scheduled_posts": scheduled_posts,
                "failed_posts": failed_posts,
                "results": results
            }
        else:
            return {
                "success": True,
                "message": f"Bulk scheduling completed. {len(scheduled_posts)} posts scheduled.",
                "scheduled_posts": scheduled_posts,
                "results": results
            }
    except Exception as e:
        logger.error(f"Error scheduling bulk composer posts: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to schedule bulk composer posts"
        )














@router.post("/social/debug/test-facebook-image-post")
async def debug_test_facebook_image_post(
    page_id: str,
    test_message: str = "Test post from debug endpoint",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint for testing Facebook image posts."""
    try:
        from app.services.facebook_service import facebook_service
        
        logger.info(f"Debug: Testing Facebook image post for user {current_user.id}")
        
        # Get page account
        page_account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook",
            SocialAccount.platform_user_id == page_id,
            SocialAccount.is_connected == True
        ).first()
        
        if not page_account:
            return {
                "success": False,
                "error": "Page not found or not connected"
            }
        
        # Generate a test image
        result = await facebook_service.generate_and_post_image(
            page_id=page_id,
            access_token=page_account.access_token,
            image_prompt="a simple test image with bright colors",
            text_content=test_message,
            post_type="feed"
        )
        
        return {
            "success": result["success"],
            "data": result if result["success"] else {"error": result.get("error")}
        }
        
    except Exception as e:
        logger.error(f"Debug test error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/social/debug/imgbb-test")
async def debug_imgbb_test(
    current_user: User = Depends(get_current_user)
):
    """Debug endpoint to test IMGBB upload functionality."""
    try:
        from app.services.image_service import image_service
        from app.config import get_settings
        import base64
        
        settings = get_settings()
        
        # Check if IMGBB is configured
        if not settings.imgbb_api_key:
            return {
                "success": False,
                "error": "IMGBB_API_KEY not configured",
                "imgbb_configured": False
            }
        
        # Create a simple test image (1x1 pixel PNG)
        test_image_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChAGAWqemowAAAABJRU5ErkJggg=="
        
        # Test IMGBB upload
        result = image_service.save_base64_image(
            base64_data=test_image_b64,
            filename="debug_test.png",
            format="png"
        )
        
        return {
            "success": result["success"],
            "imgbb_configured": True,
            "imgbb_api_key_length": len(settings.imgbb_api_key) if settings.imgbb_api_key else 0,
            "upload_result": result
        }
        
    except Exception as e:
        logger.error(f"IMGBB debug test error: {e}")
        return {
            "success": False,
            "error": str(e),
            "imgbb_configured": bool(get_settings().imgbb_api_key)
        }


@router.post("/social/debug/simple-facebook-test")
async def debug_simple_facebook_test(
    page_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Simple debug endpoint to test Facebook posting directly."""
    try:
        from app.services.facebook_service import facebook_service
        
        logger.info(f"=== SIMPLE FACEBOOK TEST ===")
        
        # Get page account
        page_account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "facebook",
            SocialAccount.platform_user_id == page_id,
            SocialAccount.is_connected == True
        ).first()
        
        if not page_account:
            return {
                "success": False,
                "error": "Page not found or not connected"
            }
        
        logger.info(f"Found page: {page_account.display_name}")
        logger.info(f"Access token: {page_account.access_token[:20]}..." if page_account.access_token else "None")
        
        # Test 1: Simple text post
        logger.info("Test 1: Simple text post")
        text_result = await facebook_service.create_post(
            page_id=page_id,
            access_token=page_account.access_token,
            message="Test post from debug endpoint - " + datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            media_type="text"
        )
        
        logger.info(f"Text post result: {text_result}")
        
        # Test 2: Image post with a simple test image
        logger.info("Test 2: Image post with test image")
        
        # Create a simple test image URL (using a public placeholder)
        test_image_url = "https://via.placeholder.com/800x600/FF0000/FFFFFF?text=Test+Image"
        
        image_result = await facebook_service.create_post(
            page_id=page_id,
            access_token=page_account.access_token,
            message="Test image post from debug endpoint - " + datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            media_url=test_image_url,
            media_type="photo"
        )
        
        logger.info(f"Image post result: {image_result}")
        
        return {
            "success": True,
            "tests": {
                "text_post": text_result,
                "image_post": image_result
            },
            "page_info": {
                "id": page_account.id,
                "name": page_account.display_name,
                "platform_id": page_account.platform_user_id,
                "has_token": bool(page_account.access_token)
            }
        }
        
    except Exception as e:
        logger.error(f"Simple test error: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/social/debug/instagram-accounts")
async def debug_instagram_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint to see all Instagram accounts for current user."""
    instagram_accounts = db.query(SocialAccount).filter(
        SocialAccount.user_id == current_user.id,
        SocialAccount.platform == "instagram"
    ).all()
    
    return {
        "user_id": current_user.id,
        "total_instagram_accounts": len(instagram_accounts),
        "accounts": [{
            "id": acc.id,
            "platform_user_id": acc.platform_user_id,
            "username": acc.username,
            "display_name": acc.display_name,
            "account_type": acc.account_type,
            "is_connected": acc.is_connected,
            "follower_count": acc.follower_count,
            "profile_picture_url": acc.profile_picture_url,
            "platform_data": acc.platform_data,
            "last_sync_at": acc.last_sync_at,
            "connected_at": acc.connected_at
        } for acc in instagram_accounts]
    }


@router.get("/social/debug/all-accounts")
async def debug_all_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint to see all social accounts for current user."""
    all_accounts = db.query(SocialAccount).filter(
        SocialAccount.user_id == current_user.id
    ).all()
    
    facebook_accounts = [acc for acc in all_accounts if acc.platform == "facebook"]
    instagram_accounts = [acc for acc in all_accounts if acc.platform == "instagram"]
    
    return {
        "user_id": current_user.id,
        "total_accounts": len(all_accounts),
        "facebook_accounts": len(facebook_accounts),
        "instagram_accounts": len(instagram_accounts),
        "accounts": [{
            "id": acc.id,
            "platform": acc.platform,
            "platform_user_id": acc.platform_user_id,
            "username": acc.username,
            "display_name": acc.display_name,
            "account_type": acc.account_type,
            "is_connected": acc.is_connected,
            "is_active": acc.is_active,
            "follower_count": acc.follower_count,
            "profile_picture_url": acc.profile_picture_url,
            "platform_data": acc.platform_data,
            "last_sync_at": acc.last_sync_at,
            "connected_at": acc.connected_at,
            "token_expires_at": acc.token_expires_at
        } for acc in all_accounts]
    }


@router.get("/social/instagram/posts-for-auto-reply/{instagram_user_id}")
async def get_instagram_posts_for_auto_reply(
    instagram_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get posts from this app for Instagram auto-reply selection."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            # Provide more helpful error message
            all_instagram_accounts = db.query(SocialAccount).filter(
                SocialAccount.user_id == current_user.id,
                SocialAccount.platform == "instagram"
            ).all()
            
            available_accounts = [acc.platform_user_id for acc in all_instagram_accounts]
            
            error_detail = f"Instagram account with ID '{instagram_user_id}' not found for current user. "
            if available_accounts:
                error_detail += f"Available Instagram accounts: {available_accounts}. "
            else:
                error_detail += "No Instagram accounts found. Please connect your Instagram account first."
            
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_detail
            )
        
        # Get posts created by this app for this account
        posts = db.query(Post).filter(
            Post.social_account_id == account.id,
            Post.status.in_([PostStatus.PUBLISHED, PostStatus.SCHEDULED])
        ).order_by(Post.created_at.desc()).limit(50).all()
        
        # Format posts for frontend
        formatted_posts = []
        for post in posts:
            formatted_posts.append({
                "id": post.id,
                "instagram_post_id": post.platform_post_id,
                "content": post.content[:200] + "..." if len(post.content) > 200 else post.content,
                "full_content": post.content,
                "created_at": post.created_at.isoformat(),
                "status": post.status.value,
                "has_media": bool(post.media_urls),
                "media_count": len(post.media_urls) if post.media_urls else 0
            })
        
        return {
            "success": True,
            "posts": formatted_posts,
            "total_count": len(formatted_posts)
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to get posts for auto-reply: {str(e)}"
        )


@router.post("/social/instagram/auto-reply")
async def toggle_instagram_auto_reply(
    request: InstagramAutoReplyToggleRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Toggle auto-reply for Instagram account with AI integration and post selection."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == request.instagram_user_id
        ).first()
        
        if not account:
            # Provide more helpful error message
            all_instagram_accounts = db.query(SocialAccount).filter(
                SocialAccount.user_id == current_user.id,
                SocialAccount.platform == "instagram"
            ).all()
            
            available_accounts = [acc.platform_user_id for acc in all_instagram_accounts]
            
            error_detail = f"Instagram account with ID '{request.instagram_user_id}' not found for current user. "
            if available_accounts:
                error_detail += f"Available Instagram accounts: {available_accounts}. "
            else:
                error_detail += "No Instagram accounts found. Please connect your Instagram account first."
            
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_detail
            )
        
        # Get the page access token from platform_data
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Page access token not found. Please reconnect your Instagram account."
            )
        
        # Validate selected posts if any
        selected_posts = []
        if request.selected_post_ids:
            posts = db.query(Post).filter(
                Post.id.in_(request.selected_post_ids),
                Post.social_account_id == account.id
            ).all()
            # Get the Instagram post IDs (platform_post_id) for the selected posts
            selected_posts = [post.platform_post_id for post in posts if post.platform_post_id]
            
            logger.info(f"Selected posts: {request.selected_post_ids}")
            logger.info(f"Instagram post IDs: {selected_posts}")
            logger.info(f"Found posts in DB: {[post.id for post in posts]}")
            logger.info(f"Platform post IDs: {[post.platform_post_id for post in posts]}")
        else:
            logger.info("No selected post IDs in request")
        
        # Use Instagram service to setup auto-reply
        instagram_result = await instagram_service.setup_auto_reply(
            instagram_user_id=request.instagram_user_id,
            page_access_token=page_access_token,
            enabled=request.enabled,
            template=request.response_template
        )
        
        # Find or create auto-reply rule in database
        auto_reply_rule = db.query(AutomationRule).filter(
            AutomationRule.user_id == current_user.id,
            AutomationRule.social_account_id == account.id,
            AutomationRule.rule_type == RuleType.AUTO_REPLY
        ).first()
        
        if auto_reply_rule:
            # Update existing rule
            auto_reply_rule.is_active = request.enabled
            rule_actions = {
                "response_template": request.response_template,
                "ai_enabled": True,
                "instagram_setup": instagram_result,
                "selected_post_ids": request.selected_post_ids,
                "selected_instagram_post_ids": selected_posts
            }
            auto_reply_rule.actions = rule_actions
            logger.info(f"🔄 Updated existing rule {auto_reply_rule.id} with actions: {rule_actions}")
        else:
            # Create new auto-reply rule
            rule_actions = {
                "response_template": request.response_template,
                "ai_enabled": True,
                "instagram_setup": instagram_result,
                "selected_post_ids": request.selected_post_ids,
                "selected_instagram_post_ids": selected_posts
            }
            auto_reply_rule = AutomationRule(
                user_id=current_user.id,
                social_account_id=account.id,
                name=f"Instagram Auto Reply - {account.display_name}",
                rule_type=RuleType.AUTO_REPLY,
                trigger_type=TriggerType.ENGAGEMENT_BASED,
                trigger_conditions={
                    "event": "comment",
                    "platform": "instagram",
                    "selected_posts": selected_posts
                },
                actions=rule_actions,
                is_active=request.enabled
            )
            db.add(auto_reply_rule)
            logger.info(f"🆕 Created new Instagram rule with actions: {rule_actions}")
        
        db.commit()
        logger.info(f"💾 Committed Instagram rule to database. Rule ID: {auto_reply_rule.id}")
        logger.info(f"💾 Final Instagram rule actions: {auto_reply_rule.actions}")
        
        return SuccessResponse(
            message=f"Instagram auto-reply {'enabled' if request.enabled else 'disabled'} successfully with AI integration",
            data={
                "rule_id": auto_reply_rule.id,
                "enabled": request.enabled,
                "ai_enabled": True,
                "account_username": account.username,
                "instagram_setup": instagram_result,
                "selected_posts_count": len(selected_posts),
                "selected_post_ids": request.selected_post_ids
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to toggle Instagram auto-reply: {str(e)}"
        )


@router.get("/social/debug/instagram-auto-reply-status")
async def debug_instagram_auto_reply_status(
    instagram_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint to check Instagram auto-reply configuration."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            return {
                "success": False,
                "error": "Instagram account not found"
            }
        
        # Get the page access token from platform_data
        page_access_token = account.platform_data.get("page_access_token")
        
        # Check for existing auto-reply rules
        auto_reply_rules = db.query(AutomationRule).filter(
            AutomationRule.user_id == current_user.id,
            AutomationRule.social_account_id == account.id,
            AutomationRule.rule_type == RuleType.AUTO_REPLY
        ).all()
        
        # Test Instagram API connection
        test_result = None
        if page_access_token:
            try:
                # Test getting recent media
                media_result = await instagram_service.get_comments(
                    instagram_user_id=instagram_user_id,
                    page_access_token=page_access_token,
                    limit=5
                )
                test_result = {
                    "success": True,
                    "media_count": len(media_result),
                    "sample_media": media_result[:2] if media_result else []
                }
            except Exception as e:
                test_result = {
                    "success": False,
                    "error": str(e)
                }
        
        return {
            "success": True,
            "account_info": {
                "id": account.id,
                "username": account.username,
                "display_name": account.display_name,
                "platform_user_id": account.platform_user_id,
                "has_page_token": bool(page_access_token),
                "page_token_length": len(page_access_token) if page_access_token else 0
            },
            "auto_reply_rules": [{
                "id": rule.id,
                "name": rule.name,
                "is_active": rule.is_active,
                "actions": rule.actions,
                "created_at": rule.created_at.isoformat() if rule.created_at else None
            } for rule in auto_reply_rules],
            "api_test": test_result,
            "total_rules": len(auto_reply_rules),
            "active_rules": len([r for r in auto_reply_rules if r.is_active])
        }
        
    except Exception as e:
        logger.error(f"Error checking Instagram auto-reply status: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/social/debug/test-instagram-comment")
async def debug_test_instagram_comment(
    instagram_user_id: str,
    media_id: str,
    comment_text: str = "Test comment from debug endpoint",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint for testing Instagram comment posting."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            return {
                "success": False,
                "error": "Instagram account not found"
            }
        
        # Get the page access token from platform_data
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            return {
                "success": False,
                "error": "Page access token not found"
            }
        
        # Test posting a comment
        result = await instagram_service.post_comment(
            media_id=media_id,
            page_access_token=page_access_token,
            comment_text=comment_text
        )
        
        return {
            "success": result["success"],
            "data": result if result["success"] else {"error": result.get("error")}
        }
        
    except Exception as e:
        logger.error(f"Debug test Instagram comment error: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/social/debug/instagram-comments/{instagram_user_id}")
async def debug_get_instagram_comments(
    instagram_user_id: str,
    media_id: Optional[str] = None,
    limit: int = 10,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint to get Instagram comments."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            return {
                "success": False,
                "error": "Instagram account not found"
            }
        
        # Get the page access token from platform_data
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            return {
                "success": False,
                "error": "Page access token not found"
            }
        
        # Get comments
        comments = await instagram_service.get_comments(
            instagram_user_id=instagram_user_id,
            page_access_token=page_access_token,
            media_id=media_id,
            limit=limit
        )
        
        return {
            "success": True,
            "comments": comments,
            "total_count": len(comments),
            "account_username": account.username
        }
        
    except Exception as e:
        logger.error(f"Error getting Instagram comments: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/social/instagram/sync-posts/{instagram_user_id}")
async def sync_instagram_posts(
    instagram_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Sync all Instagram posts from the API into the local Post table for auto-reply."""
    try:
        logger.info(f"Starting Instagram sync for user {current_user.id}, instagram_user_id: {instagram_user_id}")
        
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            logger.error(f"Instagram account not found for user {current_user.id}, instagram_user_id: {instagram_user_id}")
            raise HTTPException(status_code=404, detail="Instagram account not found")
        
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            logger.error(f"Page access token not found for account {account.id}")
            raise HTTPException(status_code=400, detail="Page access token not found. Please reconnect your Instagram account.")
        
        logger.info(f"Found Instagram account: {account.username} (ID: {account.id})")
        
        # Fetch all media from Instagram API
        from app.services.instagram_service import instagram_service
        import asyncio
        
        # Run the synchronous method in a thread pool since it's not async
        loop = asyncio.get_event_loop()
        media_items = await loop.run_in_executor(
            None, 
            instagram_service.get_user_media, 
            instagram_user_id, 
            page_access_token, 
            100
        )
        
        synced = 0
        for media in media_items:
            # Check if post already exists in DB
            existing = db.query(Post).filter(
                Post.platform_post_id == media["id"],
                Post.social_account_id == account.id
            ).first()
            if existing:
                continue  # Skip if already exists
            # Create new Post row
            post = Post(
                user_id=current_user.id,
                social_account_id=account.id,
                content=media.get("caption", ""),
                post_type=PostType.IMAGE if media.get("media_type") == "IMAGE" else (PostType.VIDEO if media.get("media_type") == "VIDEO" else PostType.TEXT),
                status=PostStatus.PUBLISHED,
                platform_post_id=media["id"],
                published_at=media.get("timestamp"),
                media_urls=[media.get("media_url")] if media.get("media_url") else None
            )
            db.add(post)
            synced += 1
        
        db.commit()
        logger.info(f"Successfully synced {synced} posts out of {len(media_items)} total media items")
        return {"success": True, "synced": synced, "total": len(media_items)}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing Instagram posts: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to sync Instagram posts: {str(e)}"
        )


@router.post("/social/debug/instagram-test-post/{instagram_user_id}")
async def debug_instagram_test_post(
    instagram_user_id: str,
    test_image_url: str = "https://www.instagram.com/static/images/ico/favicon-200.png/ab6eff595bb1.png",
    test_caption: str = "Test post from debug endpoint - please ignore",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint to test Instagram post creation with detailed error information."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            return {
                "success": False,
                "error": "Instagram account not found"
            }
        
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            return {
                "success": False,
                "error": "Page access token not found"
            }
        
        # Test media creation with detailed logging
        try:
            from app.services.instagram_service import instagram_service
            
            logger.info(f"🔍 Testing Instagram post creation for user {instagram_user_id}")
            logger.info(f"📸 Test image URL: {test_image_url}")
            logger.info(f"📝 Test caption: {test_caption}")
            logger.info(f"🔑 Token length: {len(page_access_token)}")
            
            # Test the actual post creation
            post_result = await instagram_service.create_post(
                instagram_user_id=instagram_user_id,
                page_access_token=page_access_token,
                caption=test_caption,
                image_url=test_image_url
            )
            
            return {
                "success": True,
                "post_result": post_result,
                "test_params": {
                    "instagram_user_id": instagram_user_id,
                    "image_url": test_image_url,
                    "caption": test_caption,
                    "token_length": len(page_access_token)
                }
            }
            
        except Exception as e:
            logger.error(f"❌ Instagram test post failed: {e}")
            return {
                "success": False,
                "error": str(e),
                "error_type": type(e).__name__,
                "test_params": {
                    "instagram_user_id": instagram_user_id,
                    "image_url": test_image_url,
                    "caption": test_caption,
                    "token_length": len(page_access_token)
                }
            }
        
    except Exception as e:
        logger.error(f"Error in Instagram test post: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/social/debug/instagram-api-test/{instagram_user_id}")
async def debug_instagram_api_test(
    instagram_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint to test Instagram API connectivity and permissions."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            return {
                "success": False,
                "error": "Instagram account not found"
            }
        
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            return {
                "success": False,
                "error": "Page access token not found"
            }
        
        # Test 1: Basic account info
        try:
            from app.services.instagram_service import instagram_service
            account_info = instagram_service._get_enhanced_instagram_details(
                instagram_user_id, 
                page_access_token
            )
            account_test = {
                "success": True,
                "data": account_info
            }
        except Exception as e:
            account_test = {
                "success": False,
                "error": str(e)
            }
        
        # Test 2: Get media (read permission)
        try:
            media_items = instagram_service.get_user_media(
                instagram_user_id, 
                page_access_token, 
                5
            )
            media_test = {
                "success": True,
                "count": len(media_items),
                "sample": media_items[:2] if media_items else []
            }
        except Exception as e:
            media_test = {
                "success": False,
                "error": str(e)
            }
        
        # Test 3: Test media creation with a simple image URL
        try:
            # Use a test image URL (Instagram's own logo)
            test_image_url = "https://www.instagram.com/static/images/ico/favicon-200.png/ab6eff595bb1.png"
            
            # Create a test media object (don't publish)
            media_url = f"https://graph.facebook.com/v20.0/{instagram_user_id}/media"
            media_params = {
                'access_token': page_access_token,
                'image_url': test_image_url,
                'caption': 'Test post - please ignore'
            }
            
            import requests
            response = requests.post(media_url, data=media_params)
            
            if response.status_code == 200:
                media_creation_test = {
                    "success": True,
                    "creation_id": response.json().get('id'),
                    "response": response.json()
                }
            else:
                media_creation_test = {
                    "success": False,
                    "status_code": response.status_code,
                    "error": response.text,
                    "params_sent": media_params
                }
        except Exception as e:
            media_creation_test = {
                "success": False,
                "error": str(e)
            }
        
        return {
            "account_info": account_test,
            "media_read_test": media_test,
            "media_creation_test": media_creation_test,
            "instagram_user_id": instagram_user_id,
            "token_length": len(page_access_token),
            "token_preview": f"{page_access_token[:10]}...{page_access_token[-10:]}" if len(page_access_token) > 20 else "Too short"
        }
        
    except Exception as e:
        logger.error(f"Error in Instagram API test: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.get("/social/debug/instagram-sync-test/{instagram_user_id}")
async def debug_instagram_sync_test(
    instagram_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Debug endpoint to test Instagram sync functionality."""
    try:
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == instagram_user_id
        ).first()
        
        if not account:
            return {
                "success": False,
                "error": "Instagram account not found"
            }
        
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            return {
                "success": False,
                "error": "Page access token not found"
            }
        
        # Test getting media from Instagram API
        from app.services.instagram_service import instagram_service
        import asyncio
        
        loop = asyncio.get_event_loop()
        media_items = await loop.run_in_executor(
            None, 
            instagram_service.get_user_media, 
            instagram_user_id, 
            page_access_token, 
            10  # Just get 10 items for testing
        )
        
        # Check existing posts in DB
        existing_posts = db.query(Post).filter(
            Post.social_account_id == account.id
        ).count()
        
        return {
            "success": True,
            "account_info": {
                "id": account.id,
                "username": account.username,
                "platform_user_id": account.platform_user_id
            },
            "api_test": {
                "media_items_found": len(media_items),
                "sample_media": media_items[:3] if media_items else []
            },
            "database_test": {
                "existing_posts": existing_posts
            }
        }
        
    except Exception as e:
        logger.error(f"Error in Instagram sync test: {e}")
        return {
            "success": False,
            "error": str(e)
        }


@router.post("/social/debug/facebook-message-auto-reply")
async def debug_facebook_message_auto_reply_endpoint(
    page_id: str = Body(...),
    access_token: str = Body(...),
    reply_text: str = Body("Thank you for your message! We'll get back to you soon.")
):
    """
    Debug endpoint to test Facebook message auto-reply functionality.
    """
    try:
        from app.services.facebook_message_auto_reply_service import facebook_message_auto_reply_service
        
        # Create a mock rule for testing
        mock_rule = type('MockRule', (), {
            'actions': {'message_template': reply_text}
        })()
        
        # Test the new service
        await facebook_message_auto_reply_service.process_page_messages(page_id, access_token, mock_rule)
        
        return {
            "success": True,
            "message": "Facebook message auto-reply test completed successfully"
        }
        
    except Exception as e:
        logger.error(f"Error in Facebook message auto-reply test: {e}")
        return {
            "success": False,
            "error": str(e)
        }


# LinkedIn Integration
@router.get("/social/linkedin/status")
async def get_linkedin_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get LinkedIn connection status."""
    try:
        linkedin_accounts = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "linkedin",
            SocialAccount.is_connected == True
        ).all()
        
        return {
            "connected": len(linkedin_accounts) > 0,
            "accounts": [{
                "id": acc.platform_user_id,
                "name": acc.display_name,
                "profile_picture": acc.profile_picture_url,
                "connected_at": acc.connected_at.isoformat() if acc.connected_at else None
            } for acc in linkedin_accounts]
        }
        
    except Exception as e:
        logger.error(f"Error getting LinkedIn status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get LinkedIn status: {str(e)}"
        )


@router.post("/social/linkedin/connect")
async def connect_linkedin(
    request: LinkedInConnectRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Connect LinkedIn account."""
    try:
        from app.services.linkedin_service import linkedin_service
        
        logger.info(f"LinkedIn connect request for user {current_user.id}: {request.user_id}")
        
        # Validate the access token
        validation_result = await linkedin_service.validate_access_token(request.access_token)
        if not validation_result["valid"]:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid LinkedIn token: {validation_result.get('error')}"
            )
        
        # Check if account already exists
        existing_account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "linkedin",
            SocialAccount.platform_user_id == request.user_id
        ).first()
        
        if existing_account:
            # Update existing account
            existing_account.access_token = request.access_token
            existing_account.is_connected = True
            existing_account.last_sync_at = datetime.utcnow()
            existing_account.display_name = validation_result.get("name")
            existing_account.profile_picture_url = validation_result.get("picture")
            db.commit()
            account = existing_account
        else:
            # Create new account
            account = SocialAccount(
                user_id=current_user.id,
                platform="linkedin",
                platform_user_id=request.user_id,
                access_token=request.access_token,
                account_type="personal",
                display_name=validation_result.get("name"),
                profile_picture_url=validation_result.get("picture"),
                is_connected=True,
                last_sync_at=datetime.utcnow()
            )
            db.add(account)
            db.commit()
            db.refresh(account)
        
        logger.info(f"LinkedIn account connected successfully: {account.id}")
        
        return {
            "success": True,
            "message": "LinkedIn account connected successfully",
            "data": {
                "account_id": account.id,
                "user_id": request.user_id,
                "name": validation_result.get("name"),
                "profile_picture": validation_result.get("picture")
            }
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error connecting LinkedIn account: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to connect LinkedIn account: {str(e)}"
        )


@router.post("/social/linkedin/disconnect")
async def disconnect_linkedin(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Disconnect LinkedIn account."""
    try:
        linkedin_accounts = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "linkedin",
            SocialAccount.is_connected == True
        ).all()
        
        for account in linkedin_accounts:
            account.is_connected = False
            account.access_token = None
        
        db.commit()
        
        return {
            "success": True,
            "message": f"LinkedIn account(s) disconnected successfully ({len(linkedin_accounts)} accounts)"
        }
        
    except Exception as e:
        logger.error(f"Error disconnecting LinkedIn account: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to disconnect LinkedIn account: {str(e)}"
        )


@router.post("/social/linkedin/refresh-tokens")
async def refresh_linkedin_tokens(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Refresh LinkedIn access tokens."""
    try:
        from app.services.linkedin_service import linkedin_service
        
        linkedin_accounts = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "linkedin",
            SocialAccount.is_connected == True
        ).all()
        
        refreshed_count = 0
        for account in linkedin_accounts:
            if account.refresh_token:
                refresh_result = await linkedin_service.refresh_access_token(account.refresh_token)
                if refresh_result["success"]:
                    account.access_token = refresh_result["access_token"]
                    if refresh_result.get("refresh_token"):
                        account.refresh_token = refresh_result["refresh_token"]
                    refreshed_count += 1
        
        db.commit()
        
        return {
            "success": True,
            "message": f"LinkedIn tokens refreshed successfully ({refreshed_count}/{len(linkedin_accounts)} accounts)"
        }
        
    except Exception as e:
        logger.error(f"Error refreshing LinkedIn tokens: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh LinkedIn tokens: {str(e)}"
        )

@router.get("/social/linkedin/config")
async def get_linkedin_config(
    current_user: User = Depends(get_current_user)
):
    """Get LinkedIn configuration (Client ID and Redirect URI)."""
    try:
        from app.config import get_settings
        settings = get_settings()
        
        return {
            "client_id": settings.linkedin_client_id,
            "redirect_uri": settings.linkedin_redirect_uri
        }
    except Exception as e:
        logger.error(f"Error getting LinkedIn config: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get LinkedIn configuration: {str(e)}")

# Instagram DM Auto-Reply Routes
class InstagramDmAutoReplyToggleRequest(BaseModel):
    instagram_user_id: str
    enabled: bool

@router.post("/social/instagram/dm-auto-reply")
async def toggle_instagram_dm_auto_reply(
    request: InstagramDmAutoReplyToggleRequest, 
    db: Session = Depends(get_db), 
    user: User = Depends(get_current_user)
):
    """Toggle Instagram DM auto-reply for a user."""
    try:
        from app.models.dm_auto_reply_status import DmAutoReplyStatus
        
        # Update DM auto-reply status
        status = db.query(DmAutoReplyStatus).filter_by(instagram_user_id=request.instagram_user_id).first()
        if status:
            status.enabled = request.enabled
        else:
            status = DmAutoReplyStatus(instagram_user_id=request.instagram_user_id, enabled=request.enabled, last_processed_dm_id=None)
            db.add(status)
        
        db.commit()
        
        return {
            "success": True,
            "message": f"Instagram DM auto-reply {'enabled' if request.enabled else 'disabled'} successfully",
            "enabled": request.enabled
        }
        
    except Exception as e:
        logger.error(f"Error toggling Instagram DM auto-reply: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to toggle Instagram DM auto-reply: {str(e)}"
        )

@router.get("/social/instagram/dm-auto-reply/status/{instagram_user_id}")
async def get_instagram_dm_auto_reply_status(
    instagram_user_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get Instagram DM auto-reply status for a user."""
    try:
        from app.models.dm_auto_reply_status import DmAutoReplyStatus
        
        dm_auto_reply_enabled = DmAutoReplyStatus.is_enabled(instagram_user_id, db)
        
        return {
            "success": True,
            "enabled": dm_auto_reply_enabled
        }
        
    except Exception as e:
        logger.error(f"Error getting Instagram DM auto-reply status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get Instagram DM auto-reply status: {str(e)}"
        )

# Instagram Global Auto-Reply Routes
@router.post("/social/instagram/auto_reply/global/enable")
async def enable_instagram_global_auto_reply(
    instagram_user_id: str, 
    user: User = Depends(get_current_user), 
    background_tasks: BackgroundTasks = None
):
    """Enable global auto-reply for Instagram account."""
    try:
        from app.services.instagram_auto_reply_service import enable_global_auto_reply
        
        if background_tasks:
            background_tasks.add_task(enable_global_auto_reply, instagram_user_id, user)
        else:
            await enable_global_auto_reply(instagram_user_id, user)
        
        return {
            "success": True,
            "message": "Global auto-reply enabled successfully"
        }
        
    except Exception as e:
        logger.error(f"Error enabling global auto-reply: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to enable global auto-reply: {str(e)}"
        )

@router.post("/social/instagram/auto_reply/global/disable")
async def disable_instagram_global_auto_reply(
    instagram_user_id: str, 
    user: User = Depends(get_current_user)
):
    """Disable global auto-reply for Instagram account."""
    try:
        from app.services.instagram_auto_reply_service import disable_global_auto_reply
        
        await disable_global_auto_reply(instagram_user_id, user)
        
        return {
            "success": True,
            "message": "Global auto-reply disabled successfully"
        }
        
    except Exception as e:
        logger.error(f"Error disabling global auto-reply: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to disable global auto-reply: {str(e)}"
        )

@router.get("/social/instagram/auto_reply/global/status")
async def get_instagram_global_auto_reply_status(
    instagram_user_id: str, 
    user: User = Depends(get_current_user)
):
    """Get global auto-reply status for Instagram account."""
    try:
        from app.services.instagram_auto_reply_service import get_global_auto_reply_status
        
        enabled = await get_global_auto_reply_status(instagram_user_id, user)
        
        return {
            "success": True,
            "enabled": enabled
        }
        
    except Exception as e:
        logger.error(f"Error getting global auto-reply status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get global auto-reply status: {str(e)}"
        )

@router.get("/social/instagram/auto_reply/global/progress")
async def get_global_instagram_auto_reply_progress(instagram_user_id: str):
    # Dummy implementation: always return 100% complete
    return {"progress": 100, "status": "completed"}

@router.get("/social/scheduled-posts")
def get_scheduled_posts(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    posts = db.query(ScheduledPost).filter(
        ScheduledPost.user_id == current_user.id
    ).order_by(ScheduledPost.scheduled_datetime.desc()).all()
    return [
        {
            "id": post.id,
            "prompt": post.prompt,  # UI expects 'prompt'
            "post_type": post.post_type.value if hasattr(post.post_type, "value") else post.post_type,
            "scheduled_datetime": post.scheduled_datetime.isoformat() if post.scheduled_datetime else None,
            "status": post.status,
            "media_url": post.image_url or (post.media_urls[0] if post.media_urls else None) or post.video_url,
            "platform": post.platform,
        }
        for post in posts
    ]

@router.put("/social/scheduled-posts/{post_id}")
async def update_scheduled_post(
    post_id: int,
    request: dict,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update a scheduled post (only if status is 'scheduled')."""
    try:
        # Find the scheduled post
        scheduled_post = db.query(ScheduledPost).filter(
            ScheduledPost.id == post_id,
            ScheduledPost.user_id == current_user.id
        ).first()
        
        if not scheduled_post:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Scheduled post not found"
            )
        
        # Only allow updates if the post is still scheduled
        if scheduled_post.status != "scheduled":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only update posts with 'scheduled' status"
            )
        
        # Update allowed fields
        if "prompt" in request:
            scheduled_post.prompt = request["prompt"]
        if "scheduled_datetime" in request:
            from datetime import datetime
            scheduled_post.scheduled_datetime = datetime.fromisoformat(request["scheduled_datetime"].replace('Z', '+00:00'))
        if "post_type" in request:
            scheduled_post.post_type = PostType(request["post_type"])
        
        scheduled_post.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(scheduled_post)
        
        return {
            "id": scheduled_post.id,
            "prompt": scheduled_post.prompt,
            "post_type": scheduled_post.post_type.value if hasattr(scheduled_post.post_type, "value") else scheduled_post.post_type,
            "scheduled_datetime": scheduled_post.scheduled_datetime.isoformat() if scheduled_post.scheduled_datetime else None,
            "status": scheduled_post.status,
            "media_url": scheduled_post.image_url or (scheduled_post.media_urls[0] if scheduled_post.media_urls else None) or scheduled_post.video_url,
            "platform": scheduled_post.platform,
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating scheduled post {post_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update scheduled post"
        )

@router.delete("/social/scheduled-posts/{post_id}")
async def delete_scheduled_post(
    post_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Delete a scheduled post (only if status is 'scheduled')."""
    try:
        # Find the scheduled post
        scheduled_post = db.query(ScheduledPost).filter(
            ScheduledPost.id == post_id,
            ScheduledPost.user_id == current_user.id
        ).first()
        
        if not scheduled_post:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Scheduled post not found"
            )
        
        # Only allow deletion if the post is still scheduled
        if scheduled_post.status != "scheduled":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Can only delete posts with 'scheduled' status"
            )
        
        # Delete the scheduled post
        db.delete(scheduled_post)
        db.commit()
        
        return {"success": True, "message": "Scheduled post deleted successfully"}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting scheduled post {post_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete scheduled post"
        )

@router.post("/social/instagram/bulk-schedule")
async def bulk_schedule_instagram_posts(
    social_account_id: int,
    posts: List[dict],
    db: Session = Depends(get_db),
    current_user=Depends(get_current_user)
):
    # IMPORTANT: scheduled_time is expected in IST (as selected in UI)
    ist = pytz.timezone("Asia/Kolkata")
    scheduled_posts = []
    failed_posts = []

    # Validate social account
    social_account = db.query(SocialAccount).filter(
        SocialAccount.id == social_account_id,
        SocialAccount.platform == "instagram"
    ).first()
    if not social_account:
        raise HTTPException(status_code=404, detail="Instagram account not found")

    for idx, post in enumerate(posts):
        try:
            caption = post.get("caption", "")
            scheduled_date = post.get("scheduled_date")
            scheduled_time = post.get("scheduled_time")
            post_type = post.get("post_type", "photo")
            # Add media fields as needed (image_url, media_urls, video_url, etc.)

            # Combine date and time as IST
            dt = ist.localize(datetime.strptime(f"{scheduled_date} {scheduled_time}", "%Y-%m-%d %H:%M"))

            # Set image_url for photo posts
            image_url = None
            media_urls = None
            video_url = None
            reel_thumbnail_url = None  # Add thumbnail URL field
            
            if post_type == "photo":
                image_url = post.get("media_file") or post.get("mediaPreview") or post.get("image_url")
            elif post_type == "carousel":
                media_urls = post.get("carousel_images")
            elif post_type == "reel":
                video_url = post.get("media_file") or post.get("video_url")
                # Handle thumbnail for reels
                reel_thumbnail_url = post.get("thumbnail_url") or post.get("thumbnail_file") or post.get("reel_thumbnail_url")
                
                if isinstance(video_url, str) and video_url.startswith("data:video"):
                    upload_result = cloudinary_service.upload_video_with_instagram_transform(video_url)
                    if upload_result.get("success"):
                        video_url = upload_result["url"]
                    else:
                        # handle error, e.g. skip or mark as failed
                        continue
                
                # Handle thumbnail upload if it's a base64 data URL
                if isinstance(reel_thumbnail_url, str) and reel_thumbnail_url.startswith("data:image"):
                    thumbnail_upload_result = cloudinary_service.upload_thumbnail_with_instagram_transform(reel_thumbnail_url)
                    if thumbnail_upload_result.get("success"):
                        reel_thumbnail_url = thumbnail_upload_result["url"]
                    else:
                        logger.warning(f"Failed to upload thumbnail for post {idx}: {thumbnail_upload_result.get('error')}")
                        # Continue without thumbnail rather than failing the entire post

            scheduled_post = ScheduledPost(
                user_id=current_user.id,
                social_account_id=social_account_id,
                prompt=caption,
                scheduled_datetime=dt,
                post_type=PostType(post_type.lower()),
                platform="instagram",
                status="scheduled",
                is_active=True,
                frequency=FrequencyType.DAILY,  # or set as needed
                post_time=scheduled_time,
                image_url=image_url,
                media_urls=media_urls,
                video_url=video_url,
                reel_thumbnail_url=reel_thumbnail_url,  # Add thumbnail URL to database
            )
            db.add(scheduled_post)
            scheduled_posts.append({
                "caption": caption,
                "scheduled_date": scheduled_date,
                "scheduled_time": scheduled_time,
                "scheduled_datetime": dt.isoformat(),
                "status": "scheduled",
                "post_type": post_type,
                "video_url": video_url,
                "reel_thumbnail_url": reel_thumbnail_url  # Include in response
            })
        except Exception as e:
            failed_posts.append({
                "index": idx,
                "error": str(e),
                "caption": post.get("caption", ""),
                "scheduled_date": post.get("scheduled_date"),
                "scheduled_time": post.get("scheduled_time")
            })

    db.commit()
    
    # Schedule pre-posting notifications for all successfully created posts
    try:
        from app.services.notification_service import notification_service
        # Get all the scheduled posts we just created
        created_posts = db.query(ScheduledPost).filter(
            ScheduledPost.user_id == current_user.id,
            ScheduledPost.social_account_id == social_account_id,
            ScheduledPost.status == "scheduled"
        ).order_by(ScheduledPost.id.desc()).limit(len(scheduled_posts)).all()
        
        for post in created_posts:
            try:
                await notification_service.schedule_pre_posting_alert(db, post.id)
                logger.info(f"✅ Scheduled pre-posting alert for Instagram post {post.id}")
            except Exception as notif_error:
                logger.error(f"Failed to schedule pre-posting alert for post {post.id}: {notif_error}")
    except Exception as e:
        logger.error(f"Error scheduling pre-posting notifications: {e}")
    try:
        from app.services.notification_service import notification_service
        for scheduled_post in db.query(ScheduledPost).filter(
            ScheduledPost.user_id == current_user.id,
            ScheduledPost.platform == "instagram",
            ScheduledPost.status == "scheduled"
        ).all():
            try:
                await notification_service.schedule_pre_posting_alert(db, scheduled_post.id)
            except Exception as notif_error:
                logger.error(f"Failed to schedule pre-posting alert for Instagram post {scheduled_post.id}: {notif_error}")
    except Exception as e:
        logger.error(f"Error scheduling pre-posting notifications: {e}")
    
    return {
        "success": len(failed_posts) == 0,
        "scheduled_posts": scheduled_posts,
        "failed_posts": failed_posts
    }

@router.put("/social/bulk-composer/content/{content_id}")
async def update_bulk_composer_content(
    content_id: int,
    request: BulkComposerUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update the caption of a scheduled bulk composer content item (only if status is 'scheduled')."""
    content = db.query(BulkComposerContent).filter(
        BulkComposerContent.id == content_id,
        BulkComposerContent.user_id == current_user.id
    ).first()
    if not content:
        raise HTTPException(status_code=404, detail="Content not found")
    if content.status != BulkComposerStatus.SCHEDULED.value:
        raise HTTPException(status_code=400, detail="Only scheduled posts can be edited.")
    content.caption = request.caption
    db.commit()
    db.refresh(content)
    return {
        "success": True,
        "id": content.id,
        "caption": content.caption,
        "status": content.status
    }

@router.post("/social/instagram/post-carousel")
async def create_instagram_carousel_post(
    request: InstagramCarouselPostRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Create an Instagram carousel post."""
    try:
        logger.info(f"Starting Instagram carousel post creation for user {current_user.id}")
        logger.info(f"Request data: instagram_user_id={request.instagram_user_id}, caption_length={len(request.caption)}, image_count={len(request.image_urls)}")
        # Find the Instagram account
        account = db.query(SocialAccount).filter(
            SocialAccount.user_id == current_user.id,
            SocialAccount.platform == "instagram",
            SocialAccount.platform_user_id == request.instagram_user_id
        ).first()
        if not account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Instagram account not found"
            )
        page_access_token = account.platform_data.get("page_access_token")
        if not page_access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Page access token not found. Please reconnect your Instagram account."
            )
        # Create the carousel post
        result = await instagram_service.create_carousel_post(
            instagram_user_id=request.instagram_user_id,
            page_access_token=page_access_token,
            caption=request.caption,
            image_urls=request.image_urls
        )
        if not result["success"]:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to create carousel post: {result.get('error', 'Unknown error')}"
            )
        # Save post to Post table only
        post = Post(
            user_id=current_user.id,
            social_account_id=account.id,
            content=request.caption,
            post_type=PostPostType.CAROUSEL.value,
            status=PostStatus.PUBLISHED,
            platform_post_id=result.get("post_id"),
            published_at=datetime.utcnow(),
            media_urls=request.image_urls
        )
        db.add(post)
        db.commit()
        db.refresh(post)
        return SuccessResponse(
            message="Instagram carousel post created successfully",
            data={
                "post_id": result.get("post_id"),
                "database_id": post.id,
                "platform": "instagram",
                "account_username": account.username,
                "caption": request.caption,
                "image_count": len(request.image_urls),
                "media_type": "carousel"
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error creating Instagram carousel post: {str(e)}", exc_info=True)
        import traceback
        logger.error(f"Full traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to create carousel post: {str(e)}"
        )