from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session
from typing import List, Optional
import json
import logging
import asyncio
from datetime import datetime

from app.database import get_db
from app.models.user import User
from app.models.notification import NotificationType, NotificationPlatform
from app.services.notification_service import notification_service
from app.api.auth import get_current_user
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

class NotificationResponse(BaseModel):
    id: str
    type: str
    platform: str
    strategy_name: Optional[str]
    message: str
    is_read: bool
    created_at: str
    scheduled_time: Optional[str]
    error_message: Optional[str]
    post_id: Optional[str]

class NotificationPreferencesResponse(BaseModel):
    browser_notifications_enabled: bool
    pre_posting_enabled: bool
    success_enabled: bool
    failure_enabled: bool

class NotificationPreferencesUpdate(BaseModel):
    browser_notifications_enabled: Optional[bool] = None
    pre_posting_enabled: Optional[bool] = None
    success_enabled: Optional[bool] = None
    failure_enabled: Optional[bool] = None

@router.get("/notifications")
async def get_notifications(
    limit: int = 50,
    offset: int = 0,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user notifications"""
    try:
        notifications = await notification_service.get_user_notifications(
            db=db,
            user_id=current_user.id,
            limit=limit,
            offset=offset
        )
        
        notification_data = [
            NotificationResponse(
                id=str(notification.id),
                type=notification.type.value,
                platform=notification.platform.value,
                strategy_name=notification.strategy_name,
                message=notification.message,
                is_read=notification.is_read,
                created_at=notification.created_at.isoformat(),
                scheduled_time=notification.scheduled_time.isoformat() if notification.scheduled_time else None,
                error_message=notification.error_message,
                post_id=str(notification.post_id) if notification.post_id else None
            )
            for notification in notifications
        ]
        
        return {
            "success": True,
            "data": notification_data,
            "total": len(notification_data),
            "limit": limit,
            "offset": offset
        }
        
    except Exception as e:
        import traceback
        logger.error(f"Error getting notifications for user {current_user.id}: {e}\n{traceback.format_exc()}")
        return {
            "success": False,
            "error": "Failed to get notifications",
            "data": [],
            "total": 0
        }

@router.post("/notifications/{notification_id}/mark-read")
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark a notification as read"""
    try:
        success = await notification_service.mark_notification_read(
            db=db,
            notification_id=notification_id,
            user_id=current_user.id
        )
        
        if success:
            return {"success": True, "message": "Notification marked as read"}
        else:
            raise HTTPException(status_code=404, detail="Notification not found")
            
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        logger.error(f"Error marking notification {notification_id} as read: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to mark notification as read")

@router.post("/notifications/mark-all-read")
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark all notifications as read"""
    try:
        success = await notification_service.mark_all_notifications_read(
            db=db,
            user_id=current_user.id
        )
        
        if success:
            return {"success": True, "message": "All notifications marked as read"}
        else:
            raise HTTPException(status_code=500, detail="Failed to mark notifications as read")
            
    except Exception as e:
        logger.error(f"Error marking all notifications as read for user {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to mark notifications as read")

@router.get("/notification-preferences", response_model=NotificationPreferencesResponse)
async def get_notification_preferences(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get user notification preferences"""
    try:
        preferences = await notification_service.get_user_preferences(
            db=db,
            user_id=current_user.id
        )
        
        return NotificationPreferencesResponse(
            browser_notifications_enabled=preferences.browser_notifications_enabled,
            pre_posting_enabled=preferences.pre_posting_enabled,
            success_enabled=preferences.success_enabled,
            failure_enabled=preferences.failure_enabled
        )
        
    except Exception as e:
        logger.error(f"Error getting notification preferences for user {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get notification preferences")

@router.put("/notification-preferences", response_model=NotificationPreferencesResponse)
async def update_notification_preferences(
    preferences_update: NotificationPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Update user notification preferences"""
    try:
        # Convert to dict and filter out None values
        preferences_data = {
            k: v for k, v in preferences_update.dict().items() 
            if v is not None
        }
        
        preferences = await notification_service.update_user_preferences(
            db=db,
            user_id=current_user.id,
            preferences_data=preferences_data
        )
        
        return NotificationPreferencesResponse(
            browser_notifications_enabled=preferences.browser_notifications_enabled,
            pre_posting_enabled=preferences.pre_posting_enabled,
            success_enabled=preferences.success_enabled,
            failure_enabled=preferences.failure_enabled
        )
        
    except Exception as e:
        logger.error(f"Error updating notification preferences for user {current_user.id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update notification preferences")

@router.post("/test-notification")
async def test_notification(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Test endpoint to create a sample notification"""
    try:
        await notification_service.create_notification(
            db=db,
            user_id=current_user.id,
            notification_type=NotificationType.PRE_POSTING,
            platform=NotificationPlatform.INSTAGRAM,
            message="Test notification: Your Daily Motivation strategy will be posted in 10 minutes. If you'd like to change anything before the post is made, now is the time.",
            strategy_name="Daily Motivation Test"
        )
        
        return {"success": True, "message": "Test notification created successfully"}
        
    except Exception as e:
        import traceback
        logger.error(f"Error creating test notification: {e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail="Failed to create test notification")

@router.websocket("/ws/notifications")
async def websocket_notifications(websocket: WebSocket, token: str = None):
    """WebSocket endpoint for real-time notifications with improved queue management"""
    logger.info(f"🔌 WebSocket connection attempt with token: {token[:20] if token else 'None'}...")
    
    user = None
    connection_established = False
    
    try:
        if not token:
            logger.error("❌ No token provided for WebSocket connection")
            await websocket.close(code=4001, reason="No token provided")
            return
        
        # Authenticate user using token WITHOUT holding database connection
        user = await authenticate_websocket_user(token)
        
        if not user:
            logger.error("❌ Invalid token for WebSocket connection")
            await websocket.close(code=4001, reason="Invalid token")
            return
        
        logger.info(f"✅ User authenticated: {user['email']}")
        
        # Accept the WebSocket connection
        await websocket.accept()
        connection_established = True
        logger.info(f"✅ WebSocket accepted for user {user['id']}")
        
        # Add to notification service with improved connection management
        await notification_service.add_websocket_connection(user['id'], websocket)
        logger.info(f"✅ WebSocket registered for user {user['id']} ({user['email']})")
        
        # Send a welcome message to confirm connection
        welcome_message = {
            "type": "connection_established",
            "message": "WebSocket connection established successfully",
            "user_id": user['id'],
            "timestamp": datetime.utcnow().isoformat(),
            "pending_messages": len(notification_service.pending_messages.get(user['id'], []))
        }
        
        # Use the connection wrapper to send welcome message
        if user['id'] in notification_service.websocket_connections:
            connection = notification_service.websocket_connections[user['id']]
            await connection.send_message(welcome_message)
        
        # Keep connection alive with improved message handling
        heartbeat_interval = 30  # seconds
        last_heartbeat = datetime.utcnow()
        
        try:
            while True:
                try:
                    # Wait for messages from client with timeout
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=heartbeat_interval)
                    logger.debug(f"📨 Received WebSocket message from user {user['id']}: {data}")
                    
                    # Handle different message types
                    try:
                        parsed_data = json.loads(data)
                        message_type = parsed_data.get("type", "unknown")
                        
                        if message_type == "ping":
                            # Handle ping with improved connection checking
                            if user['id'] in notification_service.websocket_connections:
                                connection = notification_service.websocket_connections[user['id']]
                                await connection.send_heartbeat()
                                last_heartbeat = datetime.utcnow()
                        
                        elif message_type == "mark_read":
                            # Handle mark as read requests
                            notification_id = parsed_data.get("notification_id")
                            if notification_id:
                                # Process mark as read (you can implement this)
                                logger.info(f"📖 Mark notification {notification_id} as read for user {user['id']}")
                        
                        else:
                            # Acknowledge other messages
                            if user['id'] in notification_service.websocket_connections:
                                connection = notification_service.websocket_connections[user['id']]
                                await connection.send_message({
                                    "type": "ack",
                                    "message": f"Received {message_type}",
                                    "timestamp": datetime.utcnow().isoformat()
                                })
                                
                    except json.JSONDecodeError:
                        logger.warning(f"⚠️ Invalid JSON from user {user['id']}: {data}")
                        
                except asyncio.TimeoutError:
                    # Send heartbeat to maintain connection
                    if user['id'] in notification_service.websocket_connections:
                        connection = notification_service.websocket_connections[user['id']]
                        success = await connection.send_heartbeat()
                        
                        if success:
                            last_heartbeat = datetime.utcnow()
                        else:
                            logger.warning(f"⚠️ Heartbeat failed for user {user['id']}, closing connection")
                            break
                    else:
                        break
                
                # Check if connection is stale
                if (datetime.utcnow() - last_heartbeat).total_seconds() > (heartbeat_interval * 3):
                    logger.warning(f"⚠️ Connection stale for user {user['id']}, closing")
                    break
                        
        except WebSocketDisconnect:
            logger.info(f"❌ WebSocket disconnected for user {user['id'] if user else 'unknown'}")
        except asyncio.CancelledError:
            logger.info(f"❌ WebSocket cancelled for user {user['id'] if user else 'unknown'}")
        except Exception as e:
            # Only log unexpected errors with full traceback
            error_str = str(e)
            if not any(x in error_str for x in ["ConnectionClosedError", "CancelledError", "ConnectionClosed"]):
                import traceback
                logger.error(f"❌ Unexpected WebSocket error for user {user['id'] if user else 'unknown'}: {e}")
                logger.error(f"❌ Traceback: {traceback.format_exc()}")
            else:
                logger.info(f"❌ WebSocket connection closed for user {user['id'] if user else 'unknown'}: {error_str}")
        
    except Exception as e:
        error_str = str(e)
        if not any(x in error_str for x in ["ConnectionClosedError", "CancelledError", "ConnectionClosed"]):
            import traceback
            logger.error(f"❌ WebSocket setup error: {e}")
            logger.error(f"❌ Full traceback: {traceback.format_exc()}")
        
        # Try to close connection gracefully
        if connection_established:
            try:
                if websocket.client_state.name not in ["DISCONNECTED", "CLOSED"]:
                    await websocket.close(code=1000, reason="Server closing connection")
            except Exception as close_error:
                logger.debug(f"❌ Error closing WebSocket (expected): {close_error}")
    
    finally:
        # Clean up WebSocket connection
        if user:
            try:
                await notification_service.remove_websocket_connection(user['id'])
                logger.info(f"✅ WebSocket cleanup completed for user {user['id']}")
            except Exception as cleanup_error:
                logger.error(f"❌ Error during WebSocket cleanup: {cleanup_error}")

async def authenticate_websocket_user(token: str):
    """Authenticate WebSocket user without holding database connection"""
    db = None
    try:
        from app.database import SessionLocal
        from app.api.auth import get_user_from_token
        
        # Create a short-lived database session
        db = SessionLocal()
        user_obj = await get_user_from_token(token, db)
        
        if user_obj:
            # Return user data as dict to avoid holding database objects
            user_data = {
                'id': user_obj.id,
                'email': user_obj.email,
                'is_active': user_obj.is_active
            }
            return user_data
        return None
        
    except Exception as e:
        logger.error(f"WebSocket authentication error: {e}")
        return None
    finally:
        if db:
            try:
                db.close()
            except Exception as close_error:
                logger.error(f"Error closing WebSocket auth database session: {close_error}")

@router.websocket("/ws/test")
async def websocket_test(websocket: WebSocket):
    """Test WebSocket endpoint without authentication"""
    logger.info("🔌 Test WebSocket connection attempt")
    
    try:
        await websocket.accept()
        logger.info("✅ Test WebSocket connected")
        
        # Send a welcome message
        await websocket.send_text("WebSocket connection established successfully!")
        
        try:
            # Keep connection alive and handle incoming messages
            while True:
                data = await websocket.receive_text()
                logger.info(f"📨 Test WebSocket message: {data}")
                # Echo the message back
                await websocket.send_text(f"Echo: {data}")
                
        except WebSocketDisconnect:
            logger.info("❌ Test WebSocket disconnected")
        except Exception as e:
            logger.error(f"❌ Test WebSocket error: {e}")
            
    except Exception as e:
        logger.error(f"❌ Test WebSocket connection error: {e}")
        try:
            await websocket.close(code=4000, reason="Connection error")
        except:
            pass