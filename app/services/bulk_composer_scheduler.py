import asyncio
import logging
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.bulk_composer_content import BulkComposerContent, BulkComposerStatus
from app.models.social_account import SocialAccount
from app.services.facebook_service import facebook_service
from app.services.cloudinary_service import cloudinary_service
from app.services.notification_service import notification_service

logger = logging.getLogger(__name__)


class BulkComposerScheduler:
    def __init__(self):
        self.is_running = False
        self.check_interval = 300  # Check every 5 minutes instead of 60 seconds
        
    async def start(self):
        """Start the bulk composer scheduler."""
        self.is_running = True
        logger.info("🚀 Starting Bulk Composer Scheduler...")
        
        # Add initial delay to prevent immediate execution
        await asyncio.sleep(10)
        
        while self.is_running:
            try:
                await self.process_due_posts()
                await asyncio.sleep(self.check_interval)
            except Exception as e:
                logger.error(f"Error in bulk composer scheduler: {str(e)}")
                await asyncio.sleep(self.check_interval)
    
    def stop(self):
        """Stop the bulk composer scheduler."""
        self.is_running = False
        logger.info("🛑 Stopping Bulk Composer Scheduler...")
    
    async def process_due_posts(self):
        """Process posts that are due to be published."""
        db = None
        try:
            # Get database session with proper context management
            from app.database import SessionLocal
            db = SessionLocal()
            
            # Find posts that are due to be published
            now = datetime.now(timezone.utc)
            due_posts = db.query(BulkComposerContent).filter(
                BulkComposerContent.status == BulkComposerStatus.SCHEDULED.value,
                BulkComposerContent.scheduled_datetime <= now
            ).all()
            
            if due_posts:
                logger.info(f"📅 Found {len(due_posts)} bulk composer posts due for publishing")
                for post in due_posts:
                    try:
                        # Create a new session for each post to avoid connection holding
                        post_db = SessionLocal()
                        try:
                            await self.publish_post(post, post_db)
                        finally:
                            post_db.close()
                    except Exception as e:
                        logger.error(f"Error publishing bulk composer post {post.id}: {e}")
                
        except Exception as e:
            logger.error(f"Error processing bulk composer due posts: {str(e)}")
        finally:
            if db:
                try:
                    db.close()
                except Exception as close_error:
                    logger.error(f"Error closing bulk composer database session: {close_error}")
    
    async def publish_post(self, post: BulkComposerContent, db: Session):
        """Publish a single post to Facebook."""
        try:
            # Get the social account
            social_account = db.query(SocialAccount).filter(
                SocialAccount.id == post.social_account_id,
                SocialAccount.is_connected == True
            ).first()
            
            if not social_account:
                logger.error(f"Social account {post.social_account_id} not found or not connected")
                post.status = BulkComposerStatus.FAILED.value
                post.error_message = "Social account not connected"
                db.commit()
                return
            
            # Update publish attempt tracking
            post.publish_attempts += 1
            post.last_publish_attempt = datetime.now(timezone.utc)

            # --- NEW LOGIC: Separate photo and text-only posts ---
            if post.media_file:
                # Photo post
                upload_result = cloudinary_service.upload_image_with_instagram_transform(post.media_file)
                if upload_result.get("success"):
                    image_url = upload_result["url"]
                else:
                    post.status = BulkComposerStatus.FAILED.value
                    post.error_message = upload_result.get("error", "Cloudinary upload failed")
                    db.commit()
                    return

                # Post to Facebook as photo
                result = await facebook_service.create_post(
                    page_id=social_account.platform_user_id,
                    access_token=social_account.access_token,
                    message=post.caption,
                    media_url=image_url,
                    media_type="photo"
                )
            else:
                # Text-only post
                result = await facebook_service.create_post(
                    page_id=social_account.platform_user_id,
                    access_token=social_account.access_token,
                    message=post.caption,
                    media_type="text"
                )

            # --- Improved error handling ---
            if result and result.get('success') and result.get('post_id'):
                post.status = BulkComposerStatus.PUBLISHED.value
                post.facebook_post_id = result.get('post_id')
                post.error_message = None
                logger.info(f"✅ Successfully published post {post.id} to Facebook: {result.get('post_id')}")
                
                # Send success notification
                try:
                    await notification_service.send_success_notification(
                        db=db,
                        post_id=post.id,
                        platform="facebook",
                        strategy_name="Bulk Scheduled Post"
                    )
                except Exception as notif_error:
                    logger.error(f"Failed to send success notification: {notif_error}")
            else:
                logger.error(f"❌ Failed to publish post {post.id}: Facebook response: {result}")
                error_message = result.get('error', 'Unknown error occurred') if result else 'No response from Facebook'
                
                # Send failure notification
                try:
                    await notification_service.send_failure_notification(
                        db=db,
                        post_id=post.id,
                        platform="facebook",
                        strategy_name="Bulk Scheduled Post",
                        error=error_message
                    )
                except Exception as notif_error:
                    logger.error(f"Failed to send failure notification: {notif_error}")
                fb_error = result.get('error') if isinstance(result, dict) else str(result)
                post.status = BulkComposerStatus.FAILED.value
                post.error_message = f"Facebook API error: {fb_error or 'No post ID returned'}"
            db.commit()
            
        except Exception as e:
            logger.error(f"❌ Error publishing post {post.id}: {str(e)}")
            post.status = BulkComposerStatus.FAILED.value
            post.error_message = str(e)
            db.commit()
    
    async def retry_failed_posts(self):
        """Retry posts that failed to publish (up to 3 attempts)."""
        db = None
        try:
            db = next(get_db())
            
            # Find failed posts with less than 3 attempts
            failed_posts = db.query(BulkComposerContent).filter(
                BulkComposerContent.status == BulkComposerStatus.FAILED.value,
                BulkComposerContent.publish_attempts < 3
            ).all()
            
            if failed_posts:
                logger.info(f"🔄 Retrying {len(failed_posts)} failed posts")
                
                for post in failed_posts:
                    # Reset status to scheduled for retry
                    post.status = BulkComposerStatus.SCHEDULED.value
                    await self.publish_post(post, db)
                    
        except Exception as e:
            logger.error(f"Error retrying failed posts: {str(e)}")
        finally:
            if db:
                db.close()


# Create a singleton instance
bulk_composer_scheduler = BulkComposerScheduler() 