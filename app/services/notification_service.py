import asyncio
import logging
import json
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any, Set
from sqlalchemy.orm import Session
from sqlalchemy import and_, desc
from collections import deque
import weakref

from app.database import get_db
from app.models.notification import Notification, NotificationPreferences, NotificationType, NotificationPlatform
from app.models.user import User
from app.models.scheduled_post import ScheduledPost

logger = logging.getLogger(__name__)

class WebSocketConnection:
    """Wrapper for WebSocket connections with queue management"""
    def __init__(self, user_id: int, websocket):
        self.user_id = user_id
        self.websocket = websocket
        self.message_queue = deque(maxlen=100)  # Limit queue size to prevent memory issues
        self.is_active = True
        self.last_heartbeat = datetime.utcnow()
        self.send_lock = asyncio.Lock()
    
    async def send_message(self, message: dict):
        """Send message with queue management and error handling"""
        if not self.is_active:
            return False
        
        async with self.send_lock:
            try:
                # Check if WebSocket is still connected
                if self.websocket.client_state.name != "CONNECTED":
                    self.is_active = False
                    return False
                
                # Add to queue first
                self.message_queue.append({
                    "message": message,
                    "timestamp": datetime.utcnow().isoformat()
                })
                
                # Send the message
                await self.websocket.send_text(json.dumps(message))
                logger.debug(f"✅ Sent WebSocket message to user {self.user_id}")
                return True
                
            except Exception as e:
                logger.error(f"❌ Error sending WebSocket message to user {self.user_id}: {e}")
                self.is_active = False
                return False
    
    async def send_heartbeat(self):
        """Send heartbeat to keep connection alive"""
        heartbeat = {
            "type": "heartbeat",
            "timestamp": datetime.utcnow().isoformat()
        }
        success = await self.send_message(heartbeat)
        if success:
            self.last_heartbeat = datetime.utcnow()
        return success
    
    def is_stale(self, timeout_minutes: int = 5) -> bool:
        """Check if connection is stale (no heartbeat for timeout_minutes)"""
        return (datetime.utcnow() - self.last_heartbeat).total_seconds() > (timeout_minutes * 60)

class NotificationService:
    def __init__(self):
        self.websocket_connections: Dict[int, WebSocketConnection] = {}  # user_id -> WebSocketConnection
        self.scheduled_alerts: Dict[int, bool] = {}  # post_id -> is_scheduled (to prevent duplicate scheduling)
        self.cleanup_task = None
        self.message_queue_task = None
        self.pending_messages: Dict[int, deque] = {}  # user_id -> message queue for offline users
        self._start_background_tasks()
    
    def _start_background_tasks(self):
        """Start background tasks for connection management"""
        try:
            # Only start tasks if there's a running event loop
            loop = asyncio.get_running_loop()
            
            if not self.cleanup_task or self.cleanup_task.done():
                self.cleanup_task = asyncio.create_task(self._cleanup_stale_connections())
            
            if not self.message_queue_task or self.message_queue_task.done():
                self.message_queue_task = asyncio.create_task(self._process_pending_messages())
                
        except RuntimeError:
            # No event loop running, tasks will be started later
            logger.info("📋 Background tasks will be started when event loop is available")
            pass
    
    async def _cleanup_stale_connections(self):
        """Background task to cleanup stale WebSocket connections"""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                stale_users = []
                for user_id, connection in self.websocket_connections.items():
                    if not connection.is_active or connection.is_stale():
                        stale_users.append(user_id)
                
                for user_id in stale_users:
                    logger.info(f"🧹 Cleaning up stale WebSocket connection for user {user_id}")
                    await self.remove_websocket_connection(user_id)
                
                if stale_users:
                    logger.info(f"🧹 Cleaned up {len(stale_users)} stale connections")
                
            except Exception as e:
                logger.error(f"❌ Error in cleanup task: {e}")
                await asyncio.sleep(120)  # Wait longer on error
    
    async def _process_pending_messages(self):
        """Background task to process pending messages for reconnected users"""
        while True:
            try:
                await asyncio.sleep(5)  # Check every 5 seconds
                
                for user_id in list(self.pending_messages.keys()):
                    if user_id in self.websocket_connections and self.pending_messages[user_id]:
                        connection = self.websocket_connections[user_id]
                        messages_to_send = list(self.pending_messages[user_id])
                        self.pending_messages[user_id].clear()
                        
                        for message in messages_to_send:
                            success = await connection.send_message(message)
                            if not success:
                                # Re-queue the message if sending failed
                                self.pending_messages[user_id].appendleft(message)
                                break
                        
                        if not self.pending_messages[user_id]:
                            del self.pending_messages[user_id]
                
            except Exception as e:
                logger.error(f"❌ Error processing pending messages: {e}")
                await asyncio.sleep(30)  # Wait longer on error
    
    async def add_websocket_connection(self, user_id: int, websocket):
        """Add a WebSocket connection for a user"""
        # Ensure background tasks are running
        await self.ensure_background_tasks_running()
        
        # Remove existing connection if any
        if user_id in self.websocket_connections:
            await self.remove_websocket_connection(user_id)
        
        # Create new connection wrapper
        connection = WebSocketConnection(user_id, websocket)
        self.websocket_connections[user_id] = connection
        
        logger.info(f"✅ Added WebSocket connection for user {user_id}")
        logger.info(f"📊 Total active WebSocket connections: {len(self.websocket_connections)}")
        
        # Send any pending messages
        if user_id in self.pending_messages and self.pending_messages[user_id]:
            logger.info(f"📨 Sending {len(self.pending_messages[user_id])} pending messages to user {user_id}")
    
    async def ensure_background_tasks_running(self):
        """Ensure background tasks are running (start them if not)"""
        try:
            if not self.cleanup_task or self.cleanup_task.done():
                self.cleanup_task = asyncio.create_task(self._cleanup_stale_connections())
                logger.info("🧹 Started connection cleanup task")
            
            if not self.message_queue_task or self.message_queue_task.done():
                self.message_queue_task = asyncio.create_task(self._process_pending_messages())
                logger.info("📨 Started message queue processing task")
                
        except Exception as e:
            logger.error(f"❌ Error starting background tasks: {e}")
    
    async def remove_websocket_connection(self, user_id: int):
        """Remove a WebSocket connection for a user"""
        if user_id in self.websocket_connections:
            connection = self.websocket_connections[user_id]
            connection.is_active = False
            del self.websocket_connections[user_id]
            logger.info(f"❌ Removed WebSocket connection for user {user_id}")
            logger.info(f"📊 Total active WebSocket connections: {len(self.websocket_connections)}")
    
    async def create_notification(
        self,
        db: Session,
        user_id: int,
        notification_type: NotificationType,
        platform: NotificationPlatform,
        message: str,
        strategy_name: Optional[str] = None,
        post_id: Optional[int] = None,
        scheduled_time: Optional[datetime] = None,
        error_message: Optional[str] = None
    ) -> Notification:
        """Create a new notification with deduplication"""
        try:
            # Check for duplicate notifications (especially for pre-posting alerts)
            if notification_type == NotificationType.PRE_POSTING and post_id:
                # Check if we already have a pre-posting notification for this post in the last 15 minutes
                cutoff_time = datetime.utcnow() - timedelta(minutes=15)
                existing_notification = db.query(Notification).filter(
                    Notification.user_id == user_id,
                    Notification.post_id == post_id,
                    Notification.type == NotificationType.PRE_POSTING,
                    Notification.created_at > cutoff_time
                ).first()
                
                if existing_notification:
                    logger.info(f"⚠️ Duplicate pre-posting notification prevented for post {post_id}, user {user_id}")
                    return existing_notification
            
            notification = Notification(
                user_id=user_id,
                post_id=post_id,
                type=notification_type,
                platform=platform,
                strategy_name=strategy_name,
                message=message,
                scheduled_time=scheduled_time,
                error_message=error_message
            )
            
            db.add(notification)
            db.commit()
            db.refresh(notification)
            
            # Send real-time notification via WebSocket
            await self.send_websocket_notification(user_id, notification)
            
            logger.info(f"Created notification {notification.id} for user {user_id}")
            return notification
            
        except Exception as e:
            logger.error(f"Error creating notification: {e}")
            db.rollback()
            raise
    
    async def send_websocket_notification(self, user_id: int, notification: Notification):
        """Send notification via WebSocket if user is connected, otherwise queue it"""
        notification_data = {
            "type": "notification",
            "notification": {
                "id": str(notification.id),
                "type": notification.type.value,
                "platform": notification.platform.value,
                "strategyName": notification.strategy_name,
                "message": notification.message,
                "timestamp": notification.created_at.isoformat(),
                "isRead": notification.is_read,
                "postId": str(notification.post_id) if notification.post_id else None,
                "scheduledTime": notification.scheduled_time.isoformat() if notification.scheduled_time else None,
                "error": notification.error_message
            }
        }
        
        if user_id in self.websocket_connections:
            connection = self.websocket_connections[user_id]
            success = await connection.send_message(notification_data)
            
            if success:
                logger.info(f"✅ Sent WebSocket notification to user {user_id}: {notification.type.value}")
            else:
                logger.warning(f"⚠️ Failed to send WebSocket notification to user {user_id}, queueing for later")
                await self.remove_websocket_connection(user_id)
                self._queue_message_for_user(user_id, notification_data)
        else:
            logger.info(f"⚠️ User {user_id} not connected to WebSocket, queueing notification")
            self._queue_message_for_user(user_id, notification_data)
    
    def _queue_message_for_user(self, user_id: int, message: dict):
        """Queue a message for a user who is not currently connected"""
        if user_id not in self.pending_messages:
            self.pending_messages[user_id] = deque(maxlen=50)  # Limit pending messages
        
        self.pending_messages[user_id].append(message)
        logger.info(f"📨 Queued message for user {user_id} (queue size: {len(self.pending_messages[user_id])})")
    
    async def schedule_pre_posting_alert(self, db: Session, post_id: int):
        """Schedule a pre-posting alert for 10 minutes before the post"""
        try:
            # Check if alert is already scheduled for this post
            if post_id in self.scheduled_alerts:
                logger.info(f"⚠️ Pre-posting alert already scheduled for post {post_id}, skipping duplicate")
                return
            
            # Mark as scheduled to prevent duplicates
            self.scheduled_alerts[post_id] = True
            
            # Try to find in ScheduledPost first
            scheduled_post = db.query(ScheduledPost).filter(ScheduledPost.id == post_id).first()
            if scheduled_post:
                await self._schedule_scheduled_post_alert(db, scheduled_post)
                return
            
            # Try to find in BulkComposerContent
            from app.models.bulk_composer_content import BulkComposerContent
            bulk_post = db.query(BulkComposerContent).filter(BulkComposerContent.id == post_id).first()
            if bulk_post:
                await self._schedule_bulk_composer_alert(db, bulk_post)
                return
                
            logger.error(f"Post {post_id} not found in ScheduledPost or BulkComposerContent")
            # Remove from scheduled alerts if post not found
            self.scheduled_alerts.pop(post_id, None)
            
        except Exception as e:
            logger.error(f"Error scheduling pre-posting alert for post {post_id}: {e}")
            # Remove from scheduled alerts on error
            self.scheduled_alerts.pop(post_id, None)
    
    async def _schedule_scheduled_post_alert(self, db: Session, scheduled_post: ScheduledPost):
        """Schedule alert for ScheduledPost"""
        try:
            
            # Check if we should send pre-posting notification
            user_prefs = await self.get_user_preferences(db, scheduled_post.user_id)
            if not user_prefs.pre_posting_enabled:
                logger.info(f"Pre-posting notifications disabled for user {scheduled_post.user_id}")
                return
            
            # Calculate 10 minutes before scheduled time
            alert_time = scheduled_post.scheduled_datetime - timedelta(minutes=10)
            
            # Handle timezone-aware comparison
            import pytz
            if scheduled_post.scheduled_datetime.tzinfo is not None:
                # If scheduled_datetime is timezone-aware, use UTC for comparison
                current_time = datetime.now(pytz.UTC)
            else:
                # If scheduled_datetime is naive, assume it's in UTC
                current_time = datetime.utcnow()
            
            # Only schedule if alert time is in the future
            if alert_time > current_time:
                # Schedule the alert (in a real implementation, you'd use a task queue like Celery)
                delay_seconds = (alert_time - current_time).total_seconds()
                asyncio.create_task(self._send_delayed_scheduled_post_alert(delay_seconds, db, scheduled_post))
                logger.info(f"✅ Scheduled pre-posting alert for Instagram post {scheduled_post.id}")
                logger.info(f"   📅 Scheduled time: {scheduled_post.scheduled_datetime}")
                logger.info(f"   ⏰ Alert time: {alert_time}")
                logger.info(f"   ⏱️ Delay: {delay_seconds} seconds ({delay_seconds/60:.1f} minutes)")
            else:
                logger.info(f"Pre-posting alert time has passed for scheduled post {scheduled_post.id}")
                
        except Exception as e:
            logger.error(f"Error scheduling pre-posting alert for scheduled post {scheduled_post.id}: {e}")
    
    async def _schedule_bulk_composer_alert(self, db: Session, bulk_post):
        """Schedule alert for BulkComposerContent"""
        try:
            # Check if we should send pre-posting notification
            user_prefs = await self.get_user_preferences(db, bulk_post.user_id)
            if not user_prefs.pre_posting_enabled:
                logger.info(f"Pre-posting notifications disabled for user {bulk_post.user_id}")
                return
            
            # Calculate 10 minutes before scheduled time
            alert_time = bulk_post.scheduled_datetime - timedelta(minutes=10)
            
            # Handle timezone-aware comparison
            import pytz
            if bulk_post.scheduled_datetime.tzinfo is not None:
                # If scheduled_datetime is timezone-aware, use UTC for comparison
                current_time = datetime.now(pytz.UTC)
            else:
                # If scheduled_datetime is naive, assume it's in UTC
                current_time = datetime.utcnow()
            
            # Only schedule if alert time is in the future
            if alert_time > current_time:
                # Schedule the alert (in a real implementation, you'd use a task queue like Celery)
                delay_seconds = (alert_time - current_time).total_seconds()
                asyncio.create_task(self._send_delayed_bulk_composer_alert(delay_seconds, db, bulk_post))
                logger.info(f"✅ Scheduled pre-posting alert for bulk composer post {bulk_post.id}")
                logger.info(f"   📅 Scheduled time: {bulk_post.scheduled_datetime}")
                logger.info(f"   ⏰ Alert time: {alert_time}")
                logger.info(f"   ⏱️ Delay: {delay_seconds} seconds ({delay_seconds/60:.1f} minutes)")
            else:
                logger.info(f"Pre-posting alert time has passed for bulk composer post {bulk_post.id}")
                
        except Exception as e:
            logger.error(f"Error scheduling pre-posting alert for bulk composer post {bulk_post.id}: {e}")
    
    async def _send_delayed_scheduled_post_alert(self, delay_seconds: float, db: Session, scheduled_post: ScheduledPost):
        """Send pre-posting alert after delay for ScheduledPost"""
        alert_db = None
        try:
            await asyncio.sleep(delay_seconds)
            
            # Create a fresh database session for the alert
            from app.database import SessionLocal
            alert_db = SessionLocal()
            
            # Re-fetch the post to ensure it's still valid
            fresh_post = alert_db.query(ScheduledPost).filter(ScheduledPost.id == scheduled_post.id).first()
            if not fresh_post:
                logger.warning(f"Scheduled post {scheduled_post.id} no longer exists")
                return
            
            # Only send if post is still scheduled
            if fresh_post.status == "scheduled" and fresh_post.is_active:
                strategy_name = getattr(fresh_post.strategy_plan, 'name', 'Scheduled Post') if hasattr(fresh_post, 'strategy_plan') and fresh_post.strategy_plan else "Scheduled Post"
                platform = NotificationPlatform.INSTAGRAM if fresh_post.platform == "instagram" else NotificationPlatform.FACEBOOK
                
                message = f"Your {strategy_name} strategy will be posted in 10 minutes. If you'd like to change anything before the post is made, now is the time."
                
                await self.create_notification(
                    db=alert_db,
                    user_id=fresh_post.user_id,
                    notification_type=NotificationType.PRE_POSTING,
                    platform=platform,
                    message=message,
                    strategy_name=strategy_name,
                    post_id=fresh_post.id,
                    scheduled_time=fresh_post.scheduled_datetime
                )
                
                logger.info(f"🔔 Sent pre-posting notification for Instagram post {fresh_post.id} to user {fresh_post.user_id}")
                
                # Clean up alert tracking after successful send
                self.scheduled_alerts.pop(fresh_post.id, None)
                
        except Exception as e:
            logger.error(f"Error sending delayed scheduled post alert: {e}")
            # Clean up alert tracking on error
            self.scheduled_alerts.pop(scheduled_post.id, None)
        finally:
            if alert_db:
                try:
                    alert_db.close()
                except Exception as close_error:
                    logger.error(f"Error closing alert database session: {close_error}")
    
    async def _send_delayed_bulk_composer_alert(self, delay_seconds: float, db: Session, bulk_post):
        """Send pre-posting alert after delay for BulkComposerContent"""
        alert_db = None
        try:
            await asyncio.sleep(delay_seconds)
            
            # Create a fresh database session for the alert
            from app.database import SessionLocal
            alert_db = SessionLocal()
            
            # Re-fetch the post to ensure it's still valid
            from app.models.bulk_composer_content import BulkComposerContent
            fresh_post = alert_db.query(BulkComposerContent).filter(BulkComposerContent.id == bulk_post.id).first()
            if not fresh_post:
                logger.warning(f"Bulk composer post {bulk_post.id} no longer exists")
                return
            
            # Only send if post is still scheduled
            if fresh_post.status == "scheduled":
                # Determine platform from social account
                platform = NotificationPlatform.FACEBOOK  # Default to Facebook for bulk composer
                if hasattr(fresh_post, 'social_account') and fresh_post.social_account:
                    if fresh_post.social_account.platform == 'instagram':
                        platform = NotificationPlatform.INSTAGRAM
                
                strategy_name = "Bulk Scheduled Post"
                message = f"Your {strategy_name} will be posted in 10 minutes. If you'd like to change anything before the post is made, now is the time."
                
                await self.create_notification(
                    db=alert_db,
                    user_id=fresh_post.user_id,
                    notification_type=NotificationType.PRE_POSTING,
                    platform=platform,
                    message=message,
                    strategy_name=strategy_name,
                    post_id=fresh_post.id,
                    scheduled_time=fresh_post.scheduled_datetime
                )
                
                logger.info(f"🔔 Sent pre-posting notification for bulk composer post {fresh_post.id} to user {fresh_post.user_id}")
                
                # Clean up alert tracking after successful send
                self.scheduled_alerts.pop(fresh_post.id, None)
                
        except Exception as e:
            logger.error(f"Error sending delayed bulk composer alert: {e}")
            # Clean up alert tracking on error
            self.scheduled_alerts.pop(bulk_post.id, None)
        finally:
            if alert_db:
                try:
                    alert_db.close()
                except Exception as close_error:
                    logger.error(f"Error closing bulk alert database session: {close_error}")
    
    async def send_success_notification(
        self,
        db: Session,
        post_id: int,
        platform: str,
        strategy_name: str
    ):
        """Send success notification when post is published"""
        success_db = None
        try:
            # Create a fresh database session for success notification
            from app.database import SessionLocal
            success_db = SessionLocal()
            
            # Try to find in ScheduledPost first
            scheduled_post = success_db.query(ScheduledPost).filter(ScheduledPost.id == post_id).first()
            if scheduled_post:
                user_id = scheduled_post.user_id
            else:
                # Try to find in BulkComposerContent
                from app.models.bulk_composer_content import BulkComposerContent
                bulk_post = success_db.query(BulkComposerContent).filter(BulkComposerContent.id == post_id).first()
                if bulk_post:
                    user_id = bulk_post.user_id
                else:
                    logger.error(f"Post {post_id} not found in ScheduledPost or BulkComposerContent")
                    return
            
            # Check if we should send success notification
            user_prefs = await self.get_user_preferences(success_db, user_id)
            if not user_prefs.success_enabled:
                logger.info(f"Success notifications disabled for user {user_id}")
                return
            
            platform_enum = NotificationPlatform.INSTAGRAM if platform == "instagram" else NotificationPlatform.FACEBOOK
            
            # Use user's local timezone (Asia/Kolkata) for display
            import pytz
            ist = pytz.timezone("Asia/Kolkata")
            current_time = datetime.now(ist)
            
            message = f"Your {strategy_name} post has been successfully published at {current_time.strftime('%I:%M %p')}."
            
            await self.create_notification(
                db=success_db,
                user_id=user_id,
                notification_type=NotificationType.SUCCESS,
                platform=platform_enum,
                message=message,
                strategy_name=strategy_name,
                post_id=post_id
            )
            
        except Exception as e:
            logger.error(f"Error sending success notification for post {post_id}: {e}")
        finally:
            if success_db:
                try:
                    success_db.close()
                except Exception as close_error:
                    logger.error(f"Error closing success notification database session: {close_error}")
    
    async def send_failure_notification(
        self,
        db: Session,
        post_id: int,
        platform: str,
        strategy_name: str,
        error: str
    ):
        """Send failure notification when post fails to publish"""
        try:
            # Try to find in ScheduledPost first
            scheduled_post = db.query(ScheduledPost).filter(ScheduledPost.id == post_id).first()
            if scheduled_post:
                user_id = scheduled_post.user_id
            else:
                # Try to find in BulkComposerContent
                from app.models.bulk_composer_content import BulkComposerContent
                bulk_post = db.query(BulkComposerContent).filter(BulkComposerContent.id == post_id).first()
                if bulk_post:
                    user_id = bulk_post.user_id
                else:
                    logger.error(f"Post {post_id} not found in ScheduledPost or BulkComposerContent")
                    return
            
            # Failure notifications are always sent (cannot be disabled)
            platform_enum = NotificationPlatform.INSTAGRAM if platform == "instagram" else NotificationPlatform.FACEBOOK
            
            message = f"Your {strategy_name} post failed to publish. Reason: {error}. Please check your settings and try again."
            
            await self.create_notification(
                db=db,
                user_id=user_id,
                notification_type=NotificationType.FAILURE,
                platform=platform_enum,
                message=message,
                strategy_name=strategy_name,
                post_id=post_id,
                error_message=error
            )
            
        except Exception as e:
            logger.error(f"Error sending failure notification for post {post_id}: {e}")
    
    async def get_user_notifications(
        self,
        db: Session,
        user_id: int,
        limit: int = 50,
        offset: int = 0
    ) -> List[Notification]:
        """Get notifications for a user"""
        try:
            notifications = db.query(Notification).filter(
                Notification.user_id == user_id
            ).order_by(
                desc(Notification.created_at)
            ).offset(offset).limit(limit).all()
            
            return notifications
            
        except Exception as e:
            logger.error(f"Error getting notifications for user {user_id}: {e}")
            return []
    
    async def mark_notification_read(self, db: Session, notification_id: str, user_id: int) -> bool:
        """Mark a notification as read"""
        try:
            notification = db.query(Notification).filter(
                and_(
                    Notification.id == notification_id,
                    Notification.user_id == user_id
                )
            ).first()
            
            if notification:
                notification.is_read = True
                db.commit()
                logger.info(f"Marked notification {notification_id} as read")
                return True
            else:
                logger.warning(f"Notification {notification_id} not found for user {user_id}")
                return False
                
        except Exception as e:
            logger.error(f"Error marking notification {notification_id} as read: {e}")
            db.rollback()
            return False
    
    async def mark_all_notifications_read(self, db: Session, user_id: int) -> bool:
        """Mark all notifications as read for a user"""
        try:
            db.query(Notification).filter(
                and_(
                    Notification.user_id == user_id,
                    Notification.is_read == False
                )
            ).update({"is_read": True})
            
            db.commit()
            logger.info(f"Marked all notifications as read for user {user_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error marking all notifications as read for user {user_id}: {e}")
            db.rollback()
            return False
    
    async def get_user_preferences(self, db: Session, user_id: int) -> NotificationPreferences:
        """Get or create user notification preferences"""
        try:
            preferences = db.query(NotificationPreferences).filter(
                NotificationPreferences.user_id == user_id
            ).first()
            
            if not preferences:
                # Create default preferences
                preferences = NotificationPreferences(user_id=user_id)
                db.add(preferences)
                db.commit()
                db.refresh(preferences)
                logger.info(f"Created default notification preferences for user {user_id}")
            
            return preferences
            
        except Exception as e:
            logger.error(f"Error getting preferences for user {user_id}: {e}")
            db.rollback()
            # Return default preferences
            return NotificationPreferences(
                user_id=user_id,
                browser_notifications_enabled=True,
                pre_posting_enabled=True,
                success_enabled=True,
                failure_enabled=True
            )
    
    async def update_user_preferences(
        self,
        db: Session,
        user_id: int,
        preferences_data: Dict[str, bool]
    ) -> NotificationPreferences:
        """Update user notification preferences"""
        try:
            preferences = await self.get_user_preferences(db, user_id)
            
            # Update preferences
            for key, value in preferences_data.items():
                if hasattr(preferences, key):
                    setattr(preferences, key, value)
            
            # Failure notifications cannot be disabled
            preferences.failure_enabled = True
            
            db.commit()
            db.refresh(preferences)
            
            logger.info(f"Updated notification preferences for user {user_id}")
            return preferences
            
        except Exception as e:
            logger.error(f"Error updating preferences for user {user_id}: {e}")
            db.rollback()
            raise
    
    async def cleanup_old_notifications(self, db: Session, days_old: int = 30):
        """Clean up notifications older than specified days"""
        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_old)
            
            deleted_count = db.query(Notification).filter(
                Notification.created_at < cutoff_date
            ).delete()
            
            db.commit()
            logger.info(f"Cleaned up {deleted_count} old notifications")
            
        except Exception as e:
            logger.error(f"Error cleaning up old notifications: {e}")
            db.rollback()
    
    def cleanup_alert_tracking(self):
        """Clean up alert tracking for posts that are no longer relevant"""
        try:
            # Clear all alert tracking (they will be re-scheduled if needed)
            cleared_count = len(self.scheduled_alerts)
            self.scheduled_alerts.clear()
            logger.info(f"Cleaned up {cleared_count} alert tracking entries")
        except Exception as e:
            logger.error(f"Error cleaning up alert tracking: {e}")

# Global notification service instance
notification_service = NotificationService()