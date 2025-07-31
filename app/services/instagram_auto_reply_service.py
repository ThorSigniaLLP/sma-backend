import logging
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from sqlalchemy.orm import Session
from app.models.automation_rule import AutomationRule, RuleType
from app.models.social_account import SocialAccount
from app.models.post import Post
from app.services.instagram_service import instagram_service, get_access_token_for_user, has_auto_reply, mark_auto_replied
from app.services.groq_service import groq_service
from app.database import get_db
import random

from app.models.global_auto_reply_status import GlobalAutoReplyStatus
from app.models.dm_auto_reply_status import DmAutoReplyStatus

logger = logging.getLogger(__name__)

# Add a global dict to track progress (for demo; use DB for production)
global_auto_reply_progress = {}

# Add a dict to track polling tasks per user
polling_tasks = {}


class InstagramAutoReplyService:
    """Service for handling automatic replies to Instagram comments."""
    
    def __init__(self):
        self.graph_api_base = "https://graph.facebook.com/v23.0"
    
    async def process_auto_replies(self, db: Session):
        """
        Process auto-replies for all active Instagram automation rules.
        This should be called periodically (e.g., every 5 minutes).
        """
        try:
            # Get all active auto-reply rules for Instagram
            auto_reply_rules = db.query(AutomationRule).filter(
                AutomationRule.rule_type == RuleType.AUTO_REPLY,
                AutomationRule.is_active == True
            ).all()
            
            # Filter for Instagram rules only
            instagram_rules = []
            for rule in auto_reply_rules:
                social_account = db.query(SocialAccount).filter(
                    SocialAccount.id == rule.social_account_id
                ).first()
                if social_account and social_account.platform == "instagram":
                    instagram_rules.append(rule)
            
            logger.info(f"🔄 Processing auto-replies for {len(instagram_rules)} active Instagram rules")
            
            if not instagram_rules:
                logger.info("📭 No active Instagram auto-reply rules found")
                return
            
            for rule in instagram_rules:
                try:
                    logger.info(f"🎯 Processing Instagram auto-reply rule {rule.id} for account {rule.social_account_id}")
                    await self._process_rule_auto_replies(rule, db)
                except Exception as e:
                    logger.error(f"❌ Error processing Instagram auto-reply rule {rule.id}: {e}")
                    # Continue with other rules even if one fails
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Error in process_auto_replies: {e}")
    
    async def _process_rule_auto_replies(self, rule: AutomationRule, db: Session):
        """Process auto-replies for a specific Instagram rule."""
        try:
            # Get the social account
            social_account = db.query(SocialAccount).filter(
                SocialAccount.id == rule.social_account_id
            ).first()
            
            if not social_account or not social_account.is_connected:
                logger.warning(f"⚠️ Instagram account {rule.social_account_id} not found or not connected")
                return
            
            logger.info(f"✅ Found connected Instagram account: {social_account.display_name}")
            
            # Get selected post IDs from the rule
            selected_post_ids = rule.actions.get("selected_instagram_post_ids", [])
            logger.info(f"🔍 Rule actions: {rule.actions}")
            logger.info(f"🔍 Selected Instagram post IDs: {selected_post_ids}")
            
            if not selected_post_ids:
                logger.warning(f"🚨 No selected_instagram_post_ids found in rule.actions for rule {rule.id}. Auto-reply will not run. Make sure to set this field with valid Instagram media IDs.")
                return
            
            logger.info(f"📋 Processing {len(selected_post_ids)} selected posts for auto-reply")
            
            # Get the last check time for this rule
            last_check = rule.last_execution_at or (datetime.utcnow() - timedelta(minutes=10))
            logger.info(f"⏰ Last check: {last_check}, checking comments since then")
            
            # Get page access token from platform_data
            page_access_token = social_account.platform_data.get("page_access_token")
            if not page_access_token:
                logger.error(f"❌ No page access token found for Instagram account {social_account.id}")
                return
            
            # Process comments for each selected post with distribution logic
            total_replies = 0
            max_replies_per_execution = 3  # Limit replies per execution to avoid spam
            
            # Shuffle the post IDs to distribute replies across different posts
            shuffled_post_ids = list(selected_post_ids)
            random.shuffle(shuffled_post_ids)
            
            for post_id in shuffled_post_ids:
                if total_replies >= max_replies_per_execution:
                    logger.info(f"🛑 Reached maximum replies per execution ({max_replies_per_execution})")
                    break
                    
                logger.info(f"📝 Processing comments for Instagram post: {post_id}")
                replies_for_post = await self._process_post_comments(
                    post_id=post_id,
                    instagram_user_id=social_account.platform_user_id,
                    page_access_token=page_access_token,
                    rule=rule,
                    last_check=last_check,
                    db=db,
                    max_replies=max_replies_per_execution - total_replies
                )
                total_replies += replies_for_post
                
                if total_replies >= max_replies_per_execution:
                    break
            
            # Update last execution time
            rule.last_execution_at = datetime.utcnow()
            db.commit()
            logger.info(f"✅ Updated last execution time for rule {rule.id}. Total replies: {total_replies}")
            
        except Exception as e:
            logger.error(f"❌ Error processing Instagram rule {rule.id}: {e}")
    
    async def _process_post_comments(
        self, 
        post_id: str, 
        instagram_user_id: str,
        page_access_token: str, 
        rule: AutomationRule,
        last_check: datetime,
        db: Session,
        max_replies: int
    ):
        """Process comments for a specific Instagram post."""
        try:
            # Get comments for this Instagram post
            comments_result = await instagram_service.get_comments(
                instagram_user_id=instagram_user_id,
                page_access_token=page_access_token,
                media_id=post_id,
                limit=25
            )
            
            if not comments_result:
                logger.info(f"📭 No comments found for Instagram post {post_id}")
                return 0
            
            # Filter comments since last check
            recent_comments = []
            for comment in comments_result:
                try:
                    # Parse timestamp - Instagram uses ISO format
                    timestamp_str = comment.get('timestamp', '')
                    if timestamp_str:
                        comment_time = self.parse_instagram_timestamp(timestamp_str)
                        if comment_time > last_check:
                            recent_comments.append(comment)
                            logger.info(f"📝 Found recent comment {comment.get('id')} from {comment_time}")
                        else:
                            logger.info(f"⏭️ Skipping old comment {comment.get('id')} from {comment_time} (last check: {last_check})")
                    else:
                        # If no timestamp, include the comment to be safe
                        recent_comments.append(comment)
                        logger.info(f"📝 Found comment {comment.get('id')} without timestamp (including for safety)")
                except Exception as time_error:
                    logger.warning(f"Failed to parse comment timestamp: {time_error}")
                    # If we can't parse the timestamp, include the comment to be safe
                    recent_comments.append(comment)
                    logger.info(f"📝 Found comment {comment.get('id')} with unparseable timestamp (including for safety)")
            
            logger.info(f"Found {len(recent_comments)} new comments for Instagram post {post_id}")
            
            # Process each recent comment
            replies_for_post = 0
            for comment in recent_comments:
                if replies_for_post >= max_replies:
                    logger.info(f"🛑 Reached maximum replies for this post ({max_replies})")
                    break
                    
                comment_id = comment.get('id')
                comment_text = comment.get('text', '')
                commenter_id = comment.get('from', {}).get('id')
                
                logger.info(f"🔄 Processing comment {comment_id}: '{comment_text[:50]}...'")
                
                # Skip comments from the account owner
                if commenter_id == instagram_user_id:
                    logger.info(f"⏭️ Skipping comment from own account")
                    continue
                
                # Check if we should reply to this comment
                should_reply = await self._should_reply_to_comment(
                    comment, 
                    page_access_token,
                    instagram_user_id,
                    db
                )
                
                if should_reply:
                    logger.info(f"✅ Will reply to Instagram comment {comment_id}")
                    # Generate and post AI reply
                    await self._generate_and_post_reply(
                        comment=comment,
                        page_access_token=page_access_token,
                        rule=rule,
                        instagram_user_id=instagram_user_id,
                        db=db
                    )
                    replies_for_post += 1
                else:
                    logger.info(f"⏭️ Skipping comment {comment_id} - no reply needed")
            
            return replies_for_post
            
        except Exception as e:
            logger.error(f"Error processing comments for Instagram post {post_id}: {e}")
            return 0
    
    async def _should_reply_to_comment(
        self, 
        comment: Dict[str, Any], 
        page_access_token: str,
        instagram_user_id: str,
        db: Session
    ) -> bool:
        """
        Determine if we should reply to an Instagram comment.
        
        Rules:
        1. If it's a new comment -> reply
        2. If we already replied to this comment -> don't reply again
        3. If it's from our own account -> don't reply
        4. If it's an AI-generated reply -> don't reply to avoid loops
        """
        try:
            comment_id = comment.get("id")
            commenter_id = comment.get("from", {}).get("id")
            comment_text = comment.get("text", "")
            
            logger.info(f"🔍 Evaluating comment {comment_id}: '{comment_text[:50]}...' from {commenter_id}")
            
            # Skip comments from our own account
            if commenter_id == instagram_user_id:
                logger.info(f"⏭️ Skipping comment from own account")
                return False
            
            # Skip AI-generated replies to avoid loops
            if self._is_ai_response(comment_text):
                logger.info(f"⏭️ Skipping AI-generated comment to avoid loops")
                return False
            
            # Check if we already replied to this comment
            if await has_auto_reply(comment_id, instagram_user_id, db):
                logger.info(f"Already replied to comment {comment_id}, skipping")
                return False
            
            # For Instagram, we'll reply to all new comments (simpler than Facebook threading)
            logger.info(f"✅ New Instagram comment {comment_id} from {commenter_id}, will reply")
            return True
                
        except Exception as e:
            logger.error(f"Error determining if should reply to Instagram comment {comment.get('id')}: {e}")
            return False
    
    def _is_ai_response(self, message: str) -> bool:
        """
        Check if a message is likely from our AI.
        Look for patterns that indicate it's our auto-reply.
        """
        if not message:
            return False
        
        # Check for common AI response patterns - be more specific
        ai_indicators = [
            "thanks for your comment",
            "we appreciate your engagement",
            "thank you for your comment",
            "we're glad you",
            "thanks for sharing",
            "we love hearing from you",
            "thanks! we're",
            "we're excited",
            "you can find it",
            "let us know if",
            "you're welcome",
            "we appreciate your",
            "thanks for the",
            "thank you so much",
            "we're so glad you",
            "we love that you",
            "feel free to reach out",
            "don't hesitate to contact",
            "we'd love to hear",
            "we're here to help"
        ]
        
        message_lower = message.lower()
        
        # Check if any AI indicator is present - be more strict
        for indicator in ai_indicators:
            if indicator in message_lower:
                logger.info(f"🤖 AI response detected: '{indicator}' found in message")
                return True
        
        # Check for our own account replies (more specific pattern)
        # Look for @mentions combined with typical AI response patterns
        if "@" in message:
            ai_mention_patterns = [
                "thanks for your comment",
                "we appreciate your",
                "thank you for",
                "we're glad you",
                "we love hearing"
            ]
            
            for pattern in ai_mention_patterns:
                if pattern in message_lower:
                    logger.info(f"🤖 AI response detected: @mention with '{pattern}'")
                    return True
        
        logger.info(f"❌ Not an AI response: {message[:50]}...")
        return False
    
    async def _has_replied_to_comment(self, comment_id: str, page_access_token: str) -> bool:
        """Check if we already replied to an Instagram comment."""
        try:
            # Store replied comment IDs with timestamps to allow expiration
            # This prevents duplicate replies to the same comment
            
            # For now, we'll use a simple in-memory cache with expiration (in production, use database)
            # In a real implementation, you'd store this in a database table
            if not hasattr(self, '_replied_comments'):
                self._replied_comments = {}
            
            # Check if comment was replied to recently (within 24 hours)
            if comment_id in self._replied_comments:
                reply_time = self._replied_comments[comment_id]
                if datetime.utcnow() - reply_time < timedelta(hours=24):
                    logger.info(f"Already replied to comment {comment_id} recently")
                    return True
                else:
                    # Remove expired entry
                    del self._replied_comments[comment_id]
                    logger.info(f"Removed expired reply tracking for comment {comment_id}")
            
            return False
                
        except Exception as e:
            logger.error(f"❌ Error checking replies for Instagram comment {comment_id}: {e}")
            return False
    
    def _mark_comment_as_replied(self, comment_id: str):
        """Mark a comment as replied to prevent duplicate replies."""
        if not hasattr(self, '_replied_comments'):
            self._replied_comments = {}
        self._replied_comments[comment_id] = datetime.utcnow()
        logger.info(f"Marked comment {comment_id} as replied at {datetime.utcnow()}")
    
    def reset_replied_comments_cache(self):
        """Reset the replied comments cache for testing purposes."""
        if hasattr(self, '_replied_comments'):
            self._replied_comments.clear()
            logger.info("🧹 Reset replied comments cache")
        else:
            self._replied_comments = {}
            logger.info("🧹 Initialized empty replied comments cache")
    
    async def _generate_and_post_reply(
        self, 
        comment: Dict[str, Any], 
        page_access_token: str, 
        rule: AutomationRule,
        instagram_user_id: str,
        db: Session
    ):
        """Generate AI reply and post it to Instagram."""
        try:
            comment_text = comment.get("text", "")
            commenter_name = comment.get("from", {}).get("username", "there")
            comment_id = comment.get("id")
            
            # Get the media ID from the comment's media_id field (if available)
            # or extract it from the comment ID structure
            media_id = comment.get("media_id")
            if not media_id:
                # Instagram comment IDs are typically in format: {media_id}_{comment_id}
                # But sometimes they're just the comment ID
                if "_" in comment_id:
                    media_id = comment_id.split("_")[0]
                else:
                    # If we can't extract media_id, we need to get it from the rule's selected posts
                    selected_post_ids = rule.actions.get("selected_instagram_post_ids", [])
                    if selected_post_ids:
                        media_id = selected_post_ids[0]  # Use the first selected post
                    else:
                        logger.error(f"❌ Cannot determine media_id for comment {comment_id}")
                        return
            
            logger.info(f"📝 Posting reply to media {media_id} for comment {comment_id}")
            
            # Generate AI reply with user mention and context
            reply_text = await self._generate_ai_reply(
                comment_text=comment_text,
                commenter_name=commenter_name,
                template=rule.actions.get("response_template")
            )
            
            # Post reply to Instagram
            reply_result = await instagram_service.reply_to_comment(
                comment_id=comment_id,
                page_access_token=page_access_token,
                message=reply_text
            )
            
            if reply_result["success"]:
                logger.info(f"✅ Auto-reply posted successfully to Instagram comment {comment_id}")
                logger.info(f"📝 Reply: {reply_text}")
                
                # Mark this comment as replied in the DB
                await mark_auto_replied(comment_id, instagram_user_id, db)
                
                # Update rule statistics
                rule.success_count += 1
                rule.last_success_at = datetime.utcnow()
                
            else:
                logger.error(f"❌ Failed to post Instagram auto-reply: {reply_result.get('error')}")
                rule.error_count += 1
                rule.last_error_at = datetime.utcnow()
                rule.last_error_message = reply_result.get('error', 'Unknown error')
                    
        except Exception as e:
            logger.error(f"Error generating/posting Instagram reply: {e}")
            rule.error_count += 1
            rule.last_error_at = datetime.utcnow()
            rule.last_error_message = str(e)
    
    async def _generate_ai_reply(
        self, 
        comment_text: str, 
        commenter_name: str, 
        template: Optional[str] = None
    ) -> str:
        """Generate AI reply mentioning the commenter."""
        try:
            # Create a context for the AI
            context = f"Instagram comment by {commenter_name}: {comment_text}"
            
            # Use the template if provided, otherwise use a default approach
            if template:
                # Use template as a guide for AI
                ai_prompt = f"""
                Generate a friendly, engaging reply to this Instagram comment. 
                The reply should mention the commenter by name and be contextual to their comment.
                
                Template guide: {template}
                Comment: {comment_text}
                Commenter: {commenter_name}
                
                Generate a natural, conversational reply that mentions the commenter.
                Keep it under 200 characters and use appropriate emojis sparingly.
                """
            else:
                # Use default AI approach for contextual conversation
                ai_prompt = f"""
                Generate a friendly, engaging reply to this Instagram comment.
                The reply should:
                1. Mention the commenter by name (e.g., "@{commenter_name}" or "Hey {commenter_name}")
                2. Be contextual and relevant to their comment
                3. Be warm, professional, and encouraging
                4. Keep it under 200 characters
                5. Use appropriate emojis sparingly
                6. Feel natural and conversational
                
                Comment: {comment_text}
                Commenter: {commenter_name}
                
                Generate a natural, conversational reply that feels like a real person responding.
                """
            
            # Generate reply using Groq AI
            ai_result = await groq_service.generate_auto_reply(comment_text, context)
            
            if ai_result["success"]:
                reply_content = ai_result["content"]
                
                # Ensure we mention the commenter
                if commenter_name.lower() not in reply_content.lower():
                    # Add mention at the beginning if not already present
                    reply_content = f"@{commenter_name} {reply_content}"
                
                return reply_content
            else:
                # Fallback reply
                return f"@{commenter_name} Thank you for your comment! We appreciate your engagement."
                
        except Exception as e:
            logger.error(f"Error generating AI reply: {e}")
            # Fallback reply
            return f"@{commenter_name} Thank you for your comment! We appreciate your engagement."

    def parse_instagram_timestamp(self, ts):
        """
        Parse Instagram timestamps like '2025-07-06T07:55:57+0000' or '2025-07-06T07:55:57Z'.
        Converts '+0000' to '+00:00' for Python compatibility.
        """
        if ts.endswith('Z'):
            ts = ts[:-1] + '+00:00'
        elif ts.endswith('+0000'):
            ts = ts[:-5] + '+00:00'
        return datetime.fromisoformat(ts)


async def handle_incoming_comment_webhook(data):
    """Process incoming Instagram webhook for new comments and auto-reply if enabled."""
    import traceback
    from app.models.global_auto_reply_status import GlobalAutoReplyStatus
    from app.models.social_account import SocialAccount
    from app.database import SessionLocal
    logger.info(f"[WEBHOOK] Received Instagram comment webhook: {data}")
    for entry in data.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "comments":
                comment = change["value"]
                logger.info(f"[WEBHOOK] Incoming comment object: {comment}")
                instagram_user_id = entry.get("id")
                media_id = comment.get("media_id")
                if not media_id and "media" in comment and "id" in comment["media"]:
                    media_id = comment["media"]["id"]
                comment_id = comment.get("id")
                comment_text = comment.get("text", "")
                with SessionLocal() as db:
                    account = db.query(SocialAccount).filter_by(platform_user_id=instagram_user_id).first()
                    if not account:
                        logger.info(f"[WEBHOOK] No SocialAccount found for instagram_user_id={instagram_user_id}, skipping comment {comment_id}")
                        continue
                    user_id = account.user_id
                    if not GlobalAutoReplyStatus.is_enabled(user_id, instagram_user_id, db):
                        logger.info(f"[WEBHOOK] Auto-reply is not enabled for user {user_id}, ig={instagram_user_id}, skipping comment {comment_id}")
                        continue
                    try:
                        page_access_token = get_access_token_for_user(instagram_user_id)
                    except Exception as e:
                        logger.error(f"[WEBHOOK] Failed to get access token for user {instagram_user_id}: {e}")
                        logger.error(traceback.format_exc())
                        continue
                    # Check if already replied
                    if await has_auto_reply(comment_id, instagram_user_id, db):
                        logger.info(f"[WEBHOOK] Already replied to comment {comment_id}, skipping.")
                        continue
                    # Skip own comments
                    commenter_id = comment.get('from', {}).get('id')
                    if commenter_id == instagram_user_id:
                        logger.info(f"[WEBHOOK] Skipping own comment {comment_id} (user_id={commenter_id})")
                        continue
                    try:
                        logger.info(f"[WEBHOOK] Triggering reply logic for comment_id={comment_id}, media_id={media_id}")
                        # Extract commenter name from the comment
                        commenter_name = comment.get("from", {}).get("username", "there")
                        context = f"Instagram comment by {commenter_name}: {comment_text}"
                        reply_result = await groq_service.generate_auto_reply(comment_text, context)
                        reply = reply_result["content"] if reply_result["success"] else f"Thank {commenter_name}, we appreciate your comment!"
                        logger.info(f"[WEBHOOK] Generated reply: {reply}")
                        api_response = await instagram_service.reply_to_comment(
                            comment_id=comment_id,
                            page_access_token=page_access_token,
                            message=reply
                        )
                        logger.info(f"[WEBHOOK] Instagram API reply response: {api_response}")
                        if api_response.get("success"):
                            await mark_auto_replied(comment_id, instagram_user_id, db)
                            logger.info(f"[WEBHOOK] Marked comment {comment_id} as replied.")
                        else:
                            logger.error(f"[WEBHOOK] Failed to post reply to comment {comment_id}: {api_response}")
                    except Exception as e:
                        logger.error(f"[WEBHOOK] Exception during reply logic for comment {comment_id}: {e}")
                        logger.error(traceback.format_exc())

async def handle_incoming_dm_webhook(data):
    """Process incoming Instagram webhook for new DMs and auto-reply if enabled."""
    import traceback
    from app.models.dm_auto_reply_status import DmAutoReplyStatus
    from app.models.social_account import SocialAccount
    from app.database import get_db

    db = next(get_db())
    logger.info("[WEBHOOK] === Start processing Instagram DM webhook ===")
    logger.debug(f"[WEBHOOK] Raw webhook data: {data}")
    try:
        if not isinstance(data, dict) or "entry" not in data:
            logger.error(f"[WEBHOOK] Invalid webhook payload: {data}")
            return {"status": "error", "detail": "Invalid payload structure"}

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if value.get("messaging_product") != "instagram":
                    continue

                message = value.get("message")
                if not message:
                    logger.info("[WEBHOOK] Skipping event without message field.")
                    continue
                if message.get("is_echo"):
                    logger.info(f"[WEBHOOK] Skipping echo message: {message}")
                    continue

                sender_id = value.get("sender", {}).get("id")
                recipient_id = value.get("recipient", {}).get("id")
                message_id = message.get("mid")
                message_text = message.get("text", "")

                logger.info(f"[WEBHOOK] New DM from {sender_id} to {recipient_id}: '{message_text}' (id={message_id})")

                # Check if auto-reply is enabled
                try:
                    status = db.query(DmAutoReplyStatus).filter_by(instagram_user_id=recipient_id).first()
                except Exception as db_err:
                    logger.error(f"[WEBHOOK] DB error when querying DmAutoReplyStatus: {db_err}")
                    logger.error(traceback.format_exc())
                    return {"status": "error", "detail": f"DB error: {db_err}"}

                if not status or not status.enabled:
                    logger.info(f"[WEBHOOK] Auto-reply disabled for {recipient_id}, skipping message {message_id}")
                    continue

                if status.last_processed_dm_id == message_id:
                    logger.info(f"[WEBHOOK] Already replied to DM {message_id}, skipping.")
                    continue

                try:
                    page_access_token = await get_access_token_for_user(recipient_id)

                    logger.info(f"[WEBHOOK] Generating AI reply for DM {message_id}...")
                    ai_result = await groq_service.generate_dm_reply(message_text)
                    reply = ai_result["content"] if ai_result["success"] else "Thanks for your message! I'll get back to you soon. 😊"
                    
                    # Enforce character limit
                    if len(reply) > 200:
                        reply = reply[:197] + "..."

                    logger.info(f"[WEBHOOK] Generated reply: {reply}")
                    send_result = await instagram_service.send_direct_message(
                        instagram_user_id=recipient_id,
                        recipient_id=sender_id,
                        page_access_token=page_access_token,
                        message=reply
                    )

                    if send_result.get("success"):
                        logger.info(f"[WEBHOOK] Sent DM auto-reply to {sender_id} for message {message_id}")
                        # Update last processed DM ID
                        status.last_processed_dm_id = message_id
                        try:
                            db.commit()
                        except Exception as commit_err:
                            logger.error(f"[WEBHOOK] DB commit failed for DM {message_id}: {commit_err}")
                            logger.error(traceback.format_exc())
                            db.rollback()
                    else:
                        logger.error(f"[WEBHOOK] Failed to send DM auto-reply to {sender_id} for message {message_id}: {send_result}")

                except Exception as e:
                    logger.error(f"[WEBHOOK] Exception during DM reply logic for message {message_id}: {e}")
                    logger.error(traceback.format_exc())

        logger.info("[WEBHOOK] === Finished processing Instagram DM webhook ===")
        return {"status": "processed"}

    except Exception as e:
        logger.error(f"[WEBHOOK] Fatal error in DM webhook handler: {e}")
        logger.error(traceback.format_exc())
        return {"status": "error", "detail": str(e)}

async def enable_global_auto_reply(instagram_user_id: str, user):
    from app.models.global_auto_reply_status import GlobalAutoReplyStatus
    from app.database import SessionLocal
    with SessionLocal() as db:
        GlobalAutoReplyStatus.set_enabled(user.id, instagram_user_id, True, db)
    page_access_token = get_access_token_for_user(instagram_user_id)
    posts = instagram_service.get_user_media(instagram_user_id, page_access_token, limit=100)
    total_posts = len(posts)
    global_auto_reply_progress[instagram_user_id] = {"status": "processing", "current_post": 0, "total_posts": total_posts, "current_comment": 0, "total_comments": 0}
    logger.info(f"Processing {total_posts} posts for auto-reply")
    for i, post in enumerate(posts, 1):
        media_id = post.get('id')
        logger.info(f"Processing post {i}/{total_posts}: {media_id}")
        comments = await instagram_service.get_comments(instagram_user_id, page_access_token, media_id=media_id, limit=100)
        logger.info(f"Found {len(comments)} comments for post {media_id}")
        total_comments = len(comments)
        global_auto_reply_progress[instagram_user_id].update({"current_post": i, "total_posts": total_posts, "current_comment": 0, "total_comments": total_comments, "current_media_id": media_id})
        for j, comment in enumerate(comments, 1):
            logger.info(f"Processing comment {j}/{len(comments)}: {comment.get('id')}")
            global_auto_reply_progress[instagram_user_id]["current_comment"] = j
            commenter_id = comment.get('from', {}).get('id')
            if commenter_id == instagram_user_id:
                continue  # Don't reply to own comment
            if not await has_auto_reply(comment['id'], instagram_user_id, next(get_db())):
                # Extract commenter name and create context
                commenter_name = comment.get("from", {}).get("username", "there")
                context = f"Instagram comment by {commenter_name}: {comment['text']}"
                
                reply_result = await groq_service.generate_auto_reply(comment['text'], context)
                reply = reply_result["content"] if reply_result["success"] else f"Thank {commenter_name}, we appreciate your comment!"
                await instagram_service.reply_to_comment(
                    comment_id=comment['id'],
                    page_access_token=page_access_token,
                    message=reply
                )
                await mark_auto_replied(comment['id'], instagram_user_id, next(get_db()))
    global_auto_reply_progress[instagram_user_id] = {"status": "done", "details": f"Processed {total_posts} posts."}
    # Start background monitoring (could be a background task, webhook, or polling)
    # await start_monitoring_comments(instagram_user_id, user) # Removed as per edit hint
    if instagram_user_id not in polling_tasks or polling_tasks[instagram_user_id].done():
        polling_tasks[instagram_user_id] = asyncio.create_task(poll_new_posts_and_comments(instagram_user_id, user))

async def disable_global_auto_reply(instagram_user_id: str, user):
    from app.models.global_auto_reply_status import GlobalAutoReplyStatus
    from app.database import SessionLocal
    with SessionLocal() as db:
        GlobalAutoReplyStatus.set_enabled(user.id, instagram_user_id, False, db)
    # await stop_monitoring_comments(instagram_user_id, user) # Removed as per edit hint

async def get_global_auto_reply_status(instagram_user_id: str, user):
    from app.database import SessionLocal
    with SessionLocal() as db:
        return GlobalAutoReplyStatus.is_enabled(user.id, instagram_user_id, db)

async def get_global_auto_reply_progress(instagram_user_id: str, user):
    # Return the current progress for this user (fast, non-blocking)
    return global_auto_reply_progress.get(instagram_user_id, {"status": "idle", "details": "No processing in progress."})

# In enable_global_auto_reply, update progress as you process posts/comments:
# Example:
# global_auto_reply_progress[instagram_user_id] = {"status": "processing", "current_post": i, "total_posts": total, ...}
# When done:
# global_auto_reply_progress[instagram_user_id] = {"status": "done", ...}


# Create a singleton instance
instagram_auto_reply_service = InstagramAutoReplyService() 

async def poll_new_posts_and_comments(instagram_user_id: str, user, interval: int = 300):
    """Background polling task to monitor for new posts/comments and auto-reply."""
    try:
        db = next(get_db())
        account = db.query(SocialAccount).filter_by(platform_user_id=instagram_user_id).first()
        my_ig_user_id = account.platform_user_id if account else None
        while GlobalAutoReplyStatus.is_enabled(user.id, instagram_user_id, db):
            page_access_token = await get_access_token_for_user(instagram_user_id)
            posts = instagram_service.get_user_media(instagram_user_id, page_access_token, limit=100)
            for post in posts:
                media_id = post.get('id')
                comments = await instagram_service.get_comments(instagram_user_id, page_access_token, media_id=media_id, limit=100)
                for comment in comments:
                    commenter_id = comment.get('from', {}).get('id')
                    if commenter_id == my_ig_user_id:
                        continue  # Don't reply to own comment
                    if not await has_auto_reply(comment['id'], instagram_user_id, db):
                        # Extract commenter name and create context
                        commenter_name = comment.get("from", {}).get("username", "there")
                        context = f"Instagram comment by {commenter_name}: {comment['text']}"
                        
                        reply_result = await groq_service.generate_auto_reply(comment['text'], context)
                        reply = reply_result["content"] if reply_result["success"] else f"Thank {commenter_name}, we appreciate your comment!"
                        await instagram_service.reply_to_comment(
                            comment_id=comment['id'],
                            page_access_token=page_access_token,
                            message=reply
                        )
                        await mark_auto_replied(comment['id'], instagram_user_id, db)
            await asyncio.sleep(interval)
    except Exception as e:
        logger.error(f"Polling error for {instagram_user_id}: {e}") 