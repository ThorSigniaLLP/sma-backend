import asyncio
import logging
from datetime import datetime, timedelta
from typing import List
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.scheduled_post import ScheduledPost, FrequencyType
from app.models.social_account import SocialAccount
from app.models.post import Post, PostStatus, PostType
from app.services.groq_service import groq_service
from app.services.facebook_service import facebook_service
from app.services.auto_reply_service import auto_reply_service
from app.services.instagram_service import instagram_service
from app.services.cloudinary_service import cloudinary_service
from app.services.notification_service import notification_service
import pytz
from pytz import timezone, UTC
import base64
import io

logger = logging.getLogger(__name__)

class SchedulerService:
    def __init__(self):
        self.running = False
        self.check_interval = 60  # Check every 60 seconds
    
    def is_base64_image(self, data):
        return data and isinstance(data, str) and data.startswith("data:image/")

    def extract_base64(self, data):
        if "," in data:
            return data.split(",", 1)[1]
        return data

    async def start(self):
        """Start the scheduler service"""
        if self.running:
            logger.info("Scheduler service already running")
            return
        
        self.running = True
        logger.info("🚀 Scheduler service started - checking every 30 seconds")
        
        # Schedule pre-posting alerts for existing scheduled posts
        await self.schedule_existing_pre_posting_alerts()
        
        while self.running:
            try:
                await self.process_scheduled_posts()
                await self.process_auto_replies()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}")
                await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Stop the scheduler service"""
        self.running = False
        logger.info("🛑 Scheduler service stopped")
    
    async def schedule_existing_pre_posting_alerts(self):
        """Schedule pre-posting alerts for existing scheduled posts"""
        try:
            from app.database import get_db_session
            
            with get_db_session() as db:
                # Find all active scheduled posts
                now_utc = datetime.utcnow()
                future_posts = db.query(ScheduledPost).filter(
                    ScheduledPost.status == "scheduled",
                    ScheduledPost.is_active == True,
                    ScheduledPost.scheduled_datetime > now_utc
                ).all()
                
                logger.info(f"🔔 Found {len(future_posts)} scheduled posts to set up pre-posting alerts")
                
                for post in future_posts:
                    try:
                        await notification_service.schedule_pre_posting_alert(db, post.id)
                        logger.info(f"✅ Scheduled pre-posting alert for existing post {post.id}")
                    except Exception as e:
                        logger.error(f"Failed to schedule pre-posting alert for existing post {post.id}: {e}")
                
                # Also check bulk composer posts
                from app.models.bulk_composer_content import BulkComposerContent
                future_bulk_posts = db.query(BulkComposerContent).filter(
                    BulkComposerContent.status == "scheduled",
                    BulkComposerContent.scheduled_datetime > now_utc
                ).all()
                
                logger.info(f"🔔 Found {len(future_bulk_posts)} bulk composer posts to set up pre-posting alerts")
                
                for post in future_bulk_posts:
                    try:
                        await notification_service.schedule_pre_posting_alert(db, post.id)
                        logger.info(f"✅ Scheduled pre-posting alert for existing bulk post {post.id}")
                    except Exception as e:
                        logger.error(f"Failed to schedule pre-posting alert for existing bulk post {post.id}: {e}")
                        
        except Exception as e:
            logger.error(f"Error scheduling existing pre-posting alerts: {e}")
    
    async def process_scheduled_posts(self):
        """Process all scheduled posts that are due for execution"""
        try:
            # Get database session with proper context management
            from app.database import SessionLocal, get_db_session
            
            # Find all scheduled Instagram posts that are due for execution
            now_local = datetime.now(timezone("Asia/Kolkata"))
            now_utc = now_local.astimezone(UTC)
            
            # Use context manager for better session handling
            with get_db_session() as db:
                # Debug: Show all scheduled posts and their statuses
                all_scheduled = db.query(ScheduledPost).filter(
                    ScheduledPost.platform == 'instagram'
                ).all()
                
                logger.info(f"🔍 DEBUG: Total Instagram posts in DB: {len(all_scheduled)}")
                for post in all_scheduled:
                    logger.info(f"   Post {post.id}: status='{post.status}', is_active={post.is_active}, scheduled_time={post.scheduled_datetime}")
                
                # Query for due posts with minimal logging to reduce noise
                due_posts = db.query(ScheduledPost).filter(
                    ScheduledPost.status.in_(['scheduled', 'ready']),
                    ScheduledPost.platform == 'instagram',
                    ScheduledPost.scheduled_datetime <= now_utc,
                    ScheduledPost.is_active == True
                ).all()
                
                logger.info(f"🔍 Query conditions: status in ['scheduled', 'ready'], platform='instagram', scheduled_datetime <= {now_utc}, is_active=True")
                logger.info(f"📊 Found {len(due_posts)} due posts out of {len(all_scheduled)} total Instagram posts")
                
                # Process each due post immediately within the same session
                if due_posts:
                    logger.info(f"📅 Found {len(due_posts)} scheduled Instagram posts due for execution")
                    for scheduled_post in due_posts:
                        try:
                            logger.info(f"🔄 Processing post {scheduled_post.id} - Current status: {scheduled_post.status}")
                            await self.execute_scheduled_instagram_post(scheduled_post, db)
                        except Exception as e:
                            logger.error(f"Failed to execute scheduled Instagram post {scheduled_post.id}: {e}")
                            import traceback
                            traceback.print_exc()

                        
        except Exception as e:
            logger.error(f"Error processing scheduled Instagram posts: {e}")

    async def generate_and_upload_image(self, prompt: str, post_type: str = "feed") -> dict:
        """Generate AI image and upload to Cloudinary"""
        try:
            logger.info(f"🎨 Generating AI image for prompt: '{prompt[:50]}...'")
            
            # Generate image using Instagram service
            image_result = await instagram_service.generate_instagram_image_with_ai(prompt, post_type)
            
            if not image_result["success"]:
                return {"success": False, "error": f"Image generation failed: {image_result.get('error')}"}
            
            # Convert base64 to image data
            image_base64 = image_result["image_base64"]
            image_data = base64.b64decode(image_base64)
            
            # Upload to Cloudinary
            upload_result = cloudinary_service.upload_image_with_instagram_transform(image_data)
            
            if not upload_result["success"]:
                return {"success": False, "error": f"Cloudinary upload failed: {upload_result.get('error')}"}
            
            logger.info(f"✅ Successfully generated and uploaded image to Cloudinary: {upload_result['url']}")
            return {
                "success": True,
                "cloudinary_url": upload_result["url"],
                "original_prompt": prompt,
                "post_type": post_type
            }
            
        except Exception as e:
            logger.error(f"Error generating and uploading image: {e}")
            return {"success": False, "error": str(e)}

    async def generate_and_upload_video(self, prompt: str) -> dict:
        """Generate AI video and upload to Cloudinary (placeholder for future implementation)"""
        try:
            logger.info(f"🎬 Generating AI video for prompt: '{prompt[:50]}...'")
            
            # TODO: Implement video generation with AI service
            # For now, return an error indicating video generation is not yet implemented
            return {
                "success": False, 
                "error": "Video generation with AI is not yet implemented. Please provide a video URL for reel posts."
            }
            
        except Exception as e:
            logger.error(f"Error generating and uploading video: {e}")
            return {"success": False, "error": str(e)}

    async def execute_scheduled_instagram_post(self, scheduled_post: ScheduledPost, db: Session):
        """Execute a single scheduled Instagram post"""
        try:
            logger.info(f"🔄 Executing scheduled Instagram post {scheduled_post.id}: '{scheduled_post.prompt[:50]}...'")
            logger.info(f"📋 Post type: {scheduled_post.post_type.value if hasattr(scheduled_post.post_type, 'value') else scheduled_post.post_type}")
            
            # Validate presence of caption
            if not scheduled_post.prompt:
                logger.error(f"❌ Scheduled post {scheduled_post.id} missing caption. Marking as failed.")
                scheduled_post.status = "failed"
                scheduled_post.is_active = False
                scheduled_post.last_executed = datetime.utcnow()
                db.commit()
                return
            
            # Check for appropriate media based on post type
            post_type = scheduled_post.post_type.value if hasattr(scheduled_post.post_type, 'value') else scheduled_post.post_type
            has_media = False
            
            # Generate and upload images if needed
            if post_type == "photo":
                # Convert base64 image_url to Cloudinary URL if needed
                if scheduled_post.image_url and self.is_base64_image(scheduled_post.image_url):
                    logger.info(f"☁️ Converting base64 image_url to Cloudinary for post {scheduled_post.id}")
                    try:
                        base64_data = self.extract_base64(scheduled_post.image_url)
                        image_data = base64.b64decode(base64_data)
                        upload_result = cloudinary_service.upload_image_with_instagram_transform(image_data)
                        if upload_result["success"]:
                            scheduled_post.image_url = upload_result["url"]
                            db.commit()
                            logger.info(f"✅ Converted and updated image_url to Cloudinary: {scheduled_post.image_url}")
                        else:
                            logger.error(f"❌ Cloudinary upload failed: {upload_result.get('error')}")
                            scheduled_post.status = "failed"
                            scheduled_post.is_active = False
                            scheduled_post.last_executed = datetime.utcnow()
                            db.commit()
                            return
                    except Exception as e:
                        logger.error(f"❌ Error converting base64 image to Cloudinary: {e}")
                        scheduled_post.status = "failed"
                        scheduled_post.is_active = False
                        scheduled_post.last_executed = datetime.utcnow()
                        db.commit()
                        return
                if not scheduled_post.image_url:
                    logger.info(f"🎨 No image URL found for photo post, generating AI image...")
                    image_result = await self.generate_and_upload_image(scheduled_post.prompt, "feed")
                    if image_result["success"]:
                        scheduled_post.image_url = image_result["cloudinary_url"]
                        logger.info(f"✅ Updated scheduled post with Cloudinary image URL: {scheduled_post.image_url}")
                    else:
                        logger.error(f"❌ Failed to generate image: {image_result.get('error')}")
                        
                        # Check if it's a rate limit error
                        error_msg = image_result.get('error', '').lower()
                        if 'rate limit' in error_msg or 'quota' in error_msg:
                            if scheduled_post.retry_count < 5:  # Max 5 retries for rate limits
                                scheduled_post.retry_count += 1
                                retry_delay = 10 * scheduled_post.retry_count  # Exponential backoff
                                logger.info(f"🔄 AI rate limit detected, rescheduling post {scheduled_post.id} for {retry_delay} minutes later (retry {scheduled_post.retry_count}/5)")
                                scheduled_post.scheduled_datetime = datetime.utcnow() + timedelta(minutes=retry_delay)
                                scheduled_post.last_executed = datetime.utcnow()
                                db.commit()
                                return
                            else:
                                logger.error(f"❌ Post {scheduled_post.id} exceeded max AI retry attempts (5), marking as failed")
                                # Fall through to mark as failed
                        else:
                            # For other errors, mark as failed
                            scheduled_post.status = "failed"
                            scheduled_post.is_active = False
                            scheduled_post.last_executed = datetime.utcnow()
                            db.commit()
                            return
                
                has_media = bool(scheduled_post.image_url)
                logger.info(f"📸 Photo post - Image URL: {scheduled_post.image_url}")
                
            elif post_type == "carousel":
                if not scheduled_post.media_urls or len(scheduled_post.media_urls) == 0:
                    logger.info(f"🎨 No media URLs found for carousel post, generating AI images...")
                    # Generate 3-5 images for carousel
                    num_images = min(5, max(3, len(scheduled_post.prompt) // 100 + 3))  # Dynamic number based on prompt length
                    carousel_urls = []
                    
                    for i in range(num_images):
                        # Create variations of the prompt for diversity
                        variation_prompt = f"{scheduled_post.prompt} - variation {i+1}"
                        image_result = await self.generate_and_upload_image(variation_prompt, "feed")
                        if image_result["success"]:
                            carousel_urls.append(image_result["cloudinary_url"])
                        else:
                            logger.error(f"❌ Failed to generate carousel image {i+1}: {image_result.get('error')}")
                    
                    if len(carousel_urls) >= 3:
                        scheduled_post.media_urls = carousel_urls
                        logger.info(f"✅ Updated scheduled post with {len(carousel_urls)} Cloudinary image URLs for carousel")
                    else:
                        logger.error(f"❌ Failed to generate enough images for carousel (got {len(carousel_urls)}/3)")
                        
                        # Check if any generation failed due to rate limits
                        if len(carousel_urls) == 0:
                            if scheduled_post.retry_count < 5:  # Max 5 retries for rate limits
                                scheduled_post.retry_count += 1
                                retry_delay = 10 * scheduled_post.retry_count  # Exponential backoff
                                logger.info(f"🔄 No carousel images generated (likely rate limit), rescheduling post {scheduled_post.id} for {retry_delay} minutes later (retry {scheduled_post.retry_count}/5)")
                                scheduled_post.scheduled_datetime = datetime.utcnow() + timedelta(minutes=retry_delay)
                                scheduled_post.last_executed = datetime.utcnow()
                                db.commit()
                                return
                            else:
                                logger.error(f"❌ Post {scheduled_post.id} exceeded max AI retry attempts (5), marking as failed")
                                # Fall through to mark as failed
                        else:
                            # Partial failure - mark as failed
                            scheduled_post.status = "failed"
                            scheduled_post.is_active = False
                            scheduled_post.last_executed = datetime.utcnow()
                            db.commit()
                            return
                
                has_media = bool(scheduled_post.media_urls and len(scheduled_post.media_urls) > 0)
                logger.info(f"🖼️ Carousel post - Media URLs: {scheduled_post.media_urls}")
                
            elif post_type == "reel":
                if not scheduled_post.video_url:
                    logger.info(f"🎬 No video URL found for reel post, attempting to generate AI video...")
                    video_result = await self.generate_and_upload_video(scheduled_post.prompt)
                    if video_result["success"]:
                        scheduled_post.video_url = video_result["cloudinary_url"]
                        logger.info(f"✅ Updated scheduled post with Cloudinary video URL: {scheduled_post.video_url}")
                    else:
                        logger.error(f"❌ Failed to generate video: {video_result.get('error')}")
                        scheduled_post.status = "failed"
                        scheduled_post.is_active = False
                        scheduled_post.last_executed = datetime.utcnow()
                        db.commit()
                        return
                
                # Handle thumbnail for reels
                if scheduled_post.reel_thumbnail_url and self.is_base64_image(scheduled_post.reel_thumbnail_url):
                    logger.info(f"☁️ Converting base64 thumbnail to Cloudinary for reel post {scheduled_post.id}")
                    try:
                        base64_data = self.extract_base64(scheduled_post.reel_thumbnail_url)
                        thumbnail_data = base64.b64decode(base64_data)
                        upload_result = cloudinary_service.upload_thumbnail_with_instagram_transform(thumbnail_data)
                        if upload_result["success"]:
                            scheduled_post.reel_thumbnail_url = upload_result["url"]
                            db.commit()
                            logger.info(f"✅ Converted and updated thumbnail to Cloudinary: {scheduled_post.reel_thumbnail_url}")
                        else:
                            logger.warning(f"⚠️ Cloudinary thumbnail upload failed: {upload_result.get('error')}")
                            # Continue without thumbnail rather than failing the entire post
                    except Exception as e:
                        logger.warning(f"⚠️ Error converting base64 thumbnail to Cloudinary: {e}")
                        # Continue without thumbnail rather than failing the entire post
                
                has_media = bool(scheduled_post.video_url)
                logger.info(f"🎬 Reel post - Video URL: {scheduled_post.video_url}, Thumbnail URL: {scheduled_post.reel_thumbnail_url}")
            
            if not has_media:
                logger.error(f"❌ Scheduled post {scheduled_post.id} missing required media for {post_type} post. Marking as failed.")
                scheduled_post.status = "failed"
                scheduled_post.is_active = False
                scheduled_post.last_executed = datetime.utcnow()
                db.commit()
                return
            
            # Get the social account
            social_account = db.query(SocialAccount).filter(
                SocialAccount.id == scheduled_post.social_account_id
            ).first()
            if not social_account:
                logger.error(f"❌ Social account {scheduled_post.social_account_id} not found in database")
                return
            if not social_account.is_connected:
                logger.error(f"❌ Social account {scheduled_post.social_account_id} ({social_account.display_name}) is not connected")
                return
            logger.info(f"✅ Found connected Instagram account: {social_account.display_name} (ID: {social_account.id})")
            
            # Get access token and Instagram user ID
            page_access_token = social_account.platform_data.get("page_access_token")
            instagram_user_id = social_account.platform_user_id
            if not page_access_token or not instagram_user_id:
                logger.error(f"❌ Missing Instagram user ID or access token for account {social_account.id}")
                scheduled_post.status = "failed"
                db.commit()
                return
            
            # Post to Instagram based on post type
            try:
                if post_type == "photo":
                    # Single photo post
                    result = await instagram_service.create_post(
                        instagram_user_id=instagram_user_id,
                        page_access_token=page_access_token,
                        caption=scheduled_post.prompt,
                        image_url=scheduled_post.image_url
                    )
                elif post_type == "carousel":
                    # Carousel post with multiple images
                    result = await instagram_service.create_carousel_post(
                        instagram_user_id=instagram_user_id,
                        page_access_token=page_access_token,
                        caption=scheduled_post.prompt,
                        image_urls=scheduled_post.media_urls
                    )
                elif post_type == "reel":
                    # Reel post with video
                    result = await instagram_service.create_post(
                        instagram_user_id=instagram_user_id,
                        page_access_token=page_access_token,
                        caption=scheduled_post.prompt,
                        video_url=scheduled_post.video_url,
                        is_reel=True,
                        thumbnail_url=scheduled_post.reel_thumbnail_url  # Add thumbnail URL for reels
                    )
                else:
                    logger.error(f"❌ Unknown post type: {post_type}")
                    scheduled_post.status = "failed"
                    db.commit()
                    return
                
                if result and result.get("success"):
                    # Update status and post_id
                    old_status = scheduled_post.status
                    scheduled_post.status = "posted"
                    scheduled_post.post_id = result.get("post_id") or result.get("creation_id")
                    
                    logger.info(f"🔄 Updating scheduled post {scheduled_post.id} status from '{old_status}' to 'posted'")
                    logger.info(f"📝 Post ID: {scheduled_post.post_id}")
                    
                    try:
                        db.commit()  # Commit status and post_id update immediately
                        logger.info(f"✅ Successfully committed status update for post {scheduled_post.id}")
                        
                        # Verify the update was persisted
                        db.refresh(scheduled_post)
                        logger.info(f"🔍 Verified status in DB: {scheduled_post.status}")
                        
                    except Exception as commit_error:
                        logger.error(f"❌ Failed to commit status update for post {scheduled_post.id}: {commit_error}")
                        db.rollback()
                        raise commit_error
                    
                    logger.info(f"✅ Successfully posted scheduled {post_type} to Instagram: {scheduled_post.id}, post_id: {scheduled_post.post_id}")
                    # Send success notification
                    try:
                        strategy_name = scheduled_post.strategy_plan.name if scheduled_post.strategy_plan else "Scheduled Post"
                        await notification_service.send_success_notification(
                            db=db,
                            post_id=scheduled_post.id,
                            platform="instagram",
                            strategy_name=strategy_name
                        )
                    except Exception as notif_error:
                        logger.error(f"Failed to send success notification: {notif_error}")
                else:
                    # Update status to failed
                    old_status = scheduled_post.status
                    error_message = result.get('error', 'Unknown error occurred') if result else 'No response from Instagram API'
                    
                    logger.error(f"❌ Failed to post {post_type} to Instagram: {error_message}")
                    
                    # Check for specific error types that might be retryable
                    if result and isinstance(error_message, str):
                        error_lower = error_message.lower()
                        
                        # Check for rate limit or temporary errors
                        if any(keyword in error_lower for keyword in ['rate limit', 'temporarily unavailable', 'try again later', 'quota exceeded']):
                            if scheduled_post.retry_count < 3:  # Max 3 retries
                                scheduled_post.retry_count += 1
                                retry_delay = 15 * scheduled_post.retry_count  # Exponential backoff
                                logger.info(f"🔄 Temporary error detected, rescheduling post {scheduled_post.id} for {retry_delay} minutes later (retry {scheduled_post.retry_count}/3)")
                                scheduled_post.scheduled_datetime = datetime.utcnow() + timedelta(minutes=retry_delay)
                                scheduled_post.last_executed = datetime.utcnow()
                                db.commit()
                                return
                            else:
                                logger.error(f"❌ Post {scheduled_post.id} exceeded max retries (3), marking as failed")
                                # Fall through to mark as failed
                        
                        # Check for media-related errors that might need media regeneration
                        elif any(keyword in error_lower for keyword in ['media', 'image', 'video', 'download', 'fetch']):
                            if scheduled_post.retry_count < 2:  # Max 2 retries for media errors
                                scheduled_post.retry_count += 1
                                logger.info(f"🔄 Media error detected, clearing media and rescheduling post {scheduled_post.id} for 5 minutes later (retry {scheduled_post.retry_count}/2)")
                                # Clear the problematic media so it gets regenerated
                                if post_type == "photo":
                                    scheduled_post.image_url = None
                                elif post_type == "carousel":
                                    scheduled_post.media_urls = None
                                elif post_type == "reel":
                                    scheduled_post.video_url = None
                                
                                scheduled_post.scheduled_datetime = datetime.utcnow() + timedelta(minutes=5)
                                scheduled_post.last_executed = datetime.utcnow()
                                db.commit()
                                return
                            else:
                                logger.error(f"❌ Post {scheduled_post.id} exceeded max media retries (2), marking as failed")
                                # Fall through to mark as failed
                    
                    # For other errors, mark as failed
                    scheduled_post.status = "failed"
                    logger.info(f"🔄 Updating scheduled post {scheduled_post.id} status from '{old_status}' to 'failed'")
                    
                    try:
                        db.commit()
                        logger.info(f"✅ Successfully committed failed status for post {scheduled_post.id}")
                    except Exception as commit_error:
                        logger.error(f"❌ Failed to commit failed status for post {scheduled_post.id}: {commit_error}")
                        db.rollback()
                    
                    # Send failure notification
                    try:
                        strategy_name = scheduled_post.strategy_plan.name if scheduled_post.strategy_plan else "Scheduled Post"
                        await notification_service.send_failure_notification(
                            db=db,
                            post_id=scheduled_post.id,
                            platform="instagram",
                            strategy_name=strategy_name,
                            error=error_message
                        )
                    except Exception as notif_error:
                        logger.error(f"Failed to send failure notification: {notif_error}")
            except Exception as ig_error:
                logger.error(f"Instagram posting error: {ig_error}")
                old_status = scheduled_post.status
                scheduled_post.status = "failed"
                logger.info(f"🔄 Updating scheduled post {scheduled_post.id} status from '{old_status}' to 'failed' due to exception")
                
                # Send failure notification for exceptions
                try:
                    strategy_name = scheduled_post.strategy_plan.name if scheduled_post.strategy_plan else "Scheduled Post"
                    await notification_service.send_failure_notification(
                        db=db,
                        post_id=scheduled_post.id,
                        platform="instagram",
                        strategy_name=strategy_name,
                        error=str(ig_error)
                    )
                except Exception as notif_error:
                    logger.error(f"Failed to send failure notification: {notif_error}")
            
            # Final cleanup - mark as inactive and set last_executed
            scheduled_post.is_active = False
            scheduled_post.last_executed = datetime.utcnow()
            
            try:
                db.commit()
                logger.info(f"✅ Scheduled Instagram post {scheduled_post.id} executed (final status: {scheduled_post.status})")
                logger.info(f"🔍 Final verification - Post {scheduled_post.id}: status={scheduled_post.status}, is_active={scheduled_post.is_active}")
            except Exception as final_commit_error:
                logger.error(f"❌ Failed to commit final status for post {scheduled_post.id}: {final_commit_error}")
                db.rollback()
        except Exception as e:
            logger.error(f"Error executing scheduled Instagram post {scheduled_post.id}: {e}")
            try:
                scheduled_post.status = "failed"
                scheduled_post.is_active = False
                scheduled_post.last_executed = datetime.utcnow()
                db.commit()
                logger.info(f"✅ Marked post {scheduled_post.id} as failed due to execution error")
            except Exception as commit_error:
                logger.error(f"❌ Failed to commit error status for post {scheduled_post.id}: {commit_error}")
                db.rollback()
    
    def calculate_next_execution(self, post_time: str, frequency: FrequencyType) -> datetime:
        """Calculate the next execution time based on frequency"""
        try:
            time_parts = post_time.split(":")
            hour = int(time_parts[0])
            minute = int(time_parts[1])
        except (ValueError, IndexError):
            # Default to current time + frequency if time parsing fails
            hour = datetime.utcnow().hour
            minute = datetime.utcnow().minute
        
        now = datetime.utcnow()
        
        if frequency == FrequencyType.DAILY:
            next_exec = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            next_exec += timedelta(days=1)
        elif frequency == FrequencyType.WEEKLY:
            next_exec = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            next_exec += timedelta(weeks=1)
        elif frequency == FrequencyType.MONTHLY:
            next_exec = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            # Add approximately 30 days for monthly
            next_exec += timedelta(days=30)
        else:
            # Default to daily
            next_exec = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            next_exec += timedelta(days=1)
        
        return next_exec

    async def process_auto_replies(self):
        """Process auto-replies for all active automation rules"""
        db: Session = None
        try:
            # Get database session with proper context management
            from app.database import SessionLocal
            db = SessionLocal()
            
            # Process Facebook auto-replies
            await auto_reply_service.process_auto_replies(db)
            
            # Process Instagram auto-replies
            try:
                from app.services.instagram_auto_reply_service import instagram_auto_reply_service
                await instagram_auto_reply_service.process_auto_replies(db)
            except ImportError:
                # Instagram auto-reply service might not exist yet
                pass
            
        except Exception as e:
            logger.error(f"Error processing auto-replies: {e}")
        finally:
            if db:
                try:
                    db.close()
                except Exception as close_error:
                    logger.error(f"Error closing auto-reply database session: {close_error}")

# Create global scheduler instance
scheduler_service = SchedulerService() 