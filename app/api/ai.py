from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional
import logging
from app.services.groq_service import groq_service
from app.api.auth import get_current_user
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["AI Content Generation"])


class ContentGenerationRequest(BaseModel):
    """Request model for content generation."""
    prompt: str = Field(..., min_length=1, max_length=500, description="User prompt for content generation")
    platform: str = Field(default="facebook", description="Target social media platform")
    content_type: str = Field(default="post", description="Type of content to generate")
    max_length: Optional[int] = Field(default=2000, description="Maximum content length")


class ContentGenerationResponse(BaseModel):
    """Response model for content generation."""
    content: str = Field(..., description="Generated content")
    success: bool = Field(..., description="Whether generation was successful")
    model_used: str = Field(..., description="AI model used for generation")
    tokens_used: int = Field(default=0, description="Number of tokens used")
    error: Optional[str] = Field(None, description="Error message if generation failed")


class AutoReplyRequest(BaseModel):
    """Request model for auto-reply generation."""
    comment: str = Field(..., min_length=1, max_length=1000, description="Original comment to reply to")
    context: Optional[str] = Field(None, max_length=500, description="Additional context for the reply")


@router.post("/generate-content", response_model=ContentGenerationResponse)
async def generate_content(
    request: ContentGenerationRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Generate social media content using AI.
    
    This endpoint uses Groq AI to generate engaging social media content
    based on user prompts. Perfect for creating Facebook posts, captions,
    and other social media content.
    
    - IMPORTANT: If you include a quote, DO NOT use any quotation marks (" or ') around it. Write the quote as plain text.
    - It should not be start or end with quotation mark("")
    - **prompt**: Your idea or topic for the content
    - **platform**: Target platform (facebook, instagram, etc.)
    - **content_type**: Type of content (post, comment, story)
    - **max_length**: Maximum character length for the content

    BAD: As Nelson Mandela once said, "The greatest glory in living lies not in never falling, but in rising every time we fall."
    GOOD: As Nelson Mandela once said, The greatest glory in living lies not in never falling, but in rising every time we fall.
    BAD: "Just a thursday chilling, rest of the week will be a day for me."
    BAD: Just a thursday chilling, rest of the week will be a day for me.


    """
    try:
        logger.info(f"Generating content for user {current_user.id} with prompt: {request.prompt[:50]}...")
        
        # Check if Groq service is available
        if not groq_service.is_available():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI content generation service is currently unavailable. Please check the Groq API key configuration."
            )
        
        # Generate content based on platform
        if request.platform.lower() == "facebook":
            result = await groq_service.generate_facebook_post(
                prompt=request.prompt,
                content_type=request.content_type,
                max_length=request.max_length
            )
        else:
            # For other platforms, use generic generation
            result = await groq_service.generate_facebook_post(
                prompt=request.prompt,
                content_type=request.content_type,
                max_length=request.max_length
            )
        
        logger.info(f"Content generation completed for user {current_user.id}, success: {result['success']}")
        
        return ContentGenerationResponse(
            content=result["content"],
            success=result["success"],
            model_used=result["model_used"],
            tokens_used=result["tokens_used"],
            error=result.get("error")
        )
        
    except Exception as e:
        logger.error(f"Error in content generation endpoint: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate content: {str(e)}"
        )


@router.post("/generate-auto-reply", response_model=ContentGenerationResponse)
async def generate_auto_reply(
    request: AutoReplyRequest,
    current_user: User = Depends(get_current_user)
):
    """
    Generate automatic reply to social media comments.
    
    This endpoint generates personalized, contextual replies to comments
    on social media posts using AI.
    
    - **comment**: The original comment to reply to
    - **context**: Additional context about your brand/business
    """
    try:
        logger.info(f"Generating auto-reply for user {current_user.id}")
        
        # Check if Groq service is available
        if not groq_service.is_available():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI auto-reply service is currently unavailable. Please check the Groq API key configuration."
            )
        
        # Generate auto-reply
        result = await groq_service.generate_auto_reply(
            original_comment=request.comment,
            context=request.context
        )
        
        logger.info(f"Auto-reply generation completed for user {current_user.id}, success: {result['success']}")
        
        return ContentGenerationResponse(
            content=result["content"],
            success=result["success"],
            model_used=result["model_used"],
            tokens_used=result["tokens_used"],
            error=result.get("error")
        )
        
    except Exception as e:
        logger.error(f"Error in auto-reply generation endpoint: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate auto-reply: {str(e)}"
        )


@router.get("/status")
async def get_ai_service_status(current_user: User = Depends(get_current_user)):
    """
    Get the status of AI services.
    
    Returns information about the availability and configuration
    of AI content generation services.
    """
    try:
        groq_available = groq_service.is_available()
        
        # Check Stability AI service
        try:
            from app.services.fb_stability_service import stability_service
            stability_available = stability_service.is_configured()
        except ImportError:
            stability_available = False
        
        return {
            "groq_service": {
                "available": groq_available,
                "status": "healthy" if groq_available else "unavailable",
                "model": "llama-3.1-8b-instant"
            },
            "stability_ai_service": {
                "available": stability_available,
                "status": "healthy" if stability_available else "unavailable",
                "model": "stable-diffusion-v1-6",
                "features": ["text-to-image", "facebook-optimized-dimensions"]
            },
            "supported_platforms": ["facebook", "instagram", "twitter"],
            "supported_content_types": ["post", "comment", "reply", "story"],
            "features": {
                "content_generation": groq_available,
                "image_generation": stability_available,
                "auto_reply": groq_available,
                "multi_platform": True,
                "customizable_prompts": True,
                "facebook_image_posts": stability_available
            }
        }
        
    except Exception as e:
        logger.error(f"Error getting AI service status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get service status: {str(e)}"
        ) 