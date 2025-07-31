import logging
import httpx
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from sqlalchemy.orm import Session
from app.models.automation_rule import AutomationRule
from app.models.social_account import SocialAccount
from app.services.groq_service import groq_service
import asyncio

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v23.0"

class FacebookMessageAutoReplyService:
    def __init__(self):
        self.conversation_sessions = {}  # Store conversation context per user
        self.http_client = httpx.AsyncClient()  # Reuse this client
        
    async def process_page_messages(self, page_id: str, access_token: str, rule: AutomationRule):
        """
        Process incoming messages for a Facebook page and provide AI-powered responses.
        This implementation works with available permissions and provides conversational AI.
        """
        try:
            # Get the page's inbox messages using available permissions
            messages = await self._get_page_messages(page_id, access_token)
            
            if not messages:
                logger.info(f"No new messages found for page {page_id}")
                return
                
            # Process all messages concurrently
            await asyncio.gather(*[
                self._process_single_message(message, page_id, access_token, rule)
                for message in messages
            ])
                
        except Exception as e:
            logger.error(f"Error processing page messages: {e}")
    
    async def _get_page_messages(self, page_id: str, access_token: str) -> List[Dict]:
        """
        Get recent messages from the page using available permissions.
        Falls back to different endpoints based on available permissions.
        """
        try:
            # Try to get messages using the page's inbox
            async with httpx.AsyncClient() as client:
                # First, try to get the page's conversations
                conv_response = await client.get(
                    f"{GRAPH_API_BASE}/{page_id}/conversations",
                    params={
                        "access_token": access_token,
                        "fields": "id,updated_time,senders,unread_count",
                        "limit": 10
                    }
                )
                
                if conv_response.status_code == 200:
                    conversations = conv_response.json().get("data", [])
                    messages = []
                    
                    for conv in conversations:
                        # Get messages for each conversation
                        conv_id = conv["id"]
                        msg_response = await client.get(
                            f"{GRAPH_API_BASE}/{conv_id}/messages",
                            params={
                                "access_token": access_token,
                                "fields": "id,from,message,created_time,to",
                                "limit": 5
                            }
                        )
                        
                        if msg_response.status_code == 200:
                            conv_messages = msg_response.json().get("data", [])
                            for msg in conv_messages:
                                # Only process messages from users (not from the page)
                                if msg.get("from", {}).get("id") != page_id:
                                    messages.append({
                                        "conversation_id": conv_id,
                                        "message_id": msg["id"],
                                        "from_user": msg["from"],
                                        "message": msg.get("message", ""),
                                        "created_time": msg.get("created_time"),
                                        "conversation": conv
                                    })
                    
                    return messages
                    
                elif conv_response.status_code == 403:
                    # Permission denied - try alternative approach
                    logger.warning(f"Permission denied for conversations. Trying alternative approach...")
                    return await self._get_messages_alternative(page_id, access_token)
                    
                else:
                    logger.warning(f"Could not fetch conversations: {conv_response.status_code} - {conv_response.text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error getting page messages: {e}")
            return []
    
    async def _get_messages_alternative(self, page_id: str, access_token: str) -> List[Dict]:
        """
        Alternative approach to get messages when conversations endpoint fails.
        This uses different endpoints that might be available.
        """
        try:
            async with httpx.AsyncClient() as client:
                # Try to get the page's feed and look for comments
                feed_response = await client.get(
                    f"{GRAPH_API_BASE}/{page_id}/feed",
                    params={
                        "access_token": access_token,
                        "fields": "id,message,comments{id,message,from,created_time}",
                        "limit": 5
                    }
                )
                
                if feed_response.status_code == 200:
                    feed_data = feed_response.json().get("data", [])
                    messages = []
                    
                    for post in feed_data:
                        comments = post.get("comments", {}).get("data", [])
                        for comment in comments:
                            # Only process comments from users (not from the page)
                            if comment.get("from", {}).get("id") != page_id:
                                messages.append({
                                    "conversation_id": f"post_{post['id']}",
                                    "message_id": comment["id"],
                                    "from_user": comment["from"],
                                    "message": comment.get("message", ""),
                                    "created_time": comment.get("created_time"),
                                    "type": "comment"
                                })
                    
                    return messages
                    
                else:
                    logger.warning(f"Alternative approach also failed: {feed_response.status_code}")
                    return []
                    
        except Exception as e:
            logger.error(f"Error in alternative message retrieval: {e}")
            return []
    
    async def _process_single_message(self, message: Dict, page_id: str, access_token: str, rule: AutomationRule):
        """
        Process a single message and generate an AI response.
        """
        try:
            user_id = message["from_user"]["id"]
            user_name = message["from_user"].get("name", "User")
            message_text = message["message"]
            conversation_id = message["conversation_id"]
            message_type = message.get("type", "message")
            
            # Check if we should respond to this message
            if not await self._should_respond_to_message(message, page_id, access_token):
                logger.info(f"Skipping message from {user_name} - already responded or not eligible")
                return
            
            # Get conversation context for this user
            conversation_context = await self._get_conversation_context(user_id, conversation_id, page_id, access_token)
            
            # Generate AI response
            ai_response = await self._generate_conversational_response(
                user_name=user_name,
                message=message_text,
                conversation_context=conversation_context,
                rule=rule
            )
            
            # Send the response based on message type
            success = False
            if message_type == "comment":
                success = await self._send_comment_response(
                    comment_id=message["message_id"],
                    message=ai_response,
                    access_token=access_token
                )
            else:
                success = await self._send_message_response(
                    conversation_id=conversation_id,
                    message=ai_response,
                    access_token=access_token
                )
            
            if success:
                # Update conversation session
                self._update_conversation_session(user_id, message_text, ai_response)
                logger.info(f"✅ Sent AI response to {user_name}: {ai_response[:50]}...")
            else:
                logger.error(f"❌ Failed to send response to {user_name}")
                
        except Exception as e:
            logger.error(f"Error processing single message: {e}")
    
    async def _should_respond_to_message(self, message: Dict, page_id: str, access_token: str) -> bool:
        """
        Determine if we should respond to this message.
        """
        try:
            user_id = message["from_user"]["id"]
            conversation_id = message["conversation_id"]
            message_type = message.get("type", "message")
            
            if message_type == "comment":
                # For comments, check if we've already replied
                return not await self._has_replied_to_comment(message["message_id"], access_token)
            
            # For messages, check if we've already responded
            async with httpx.AsyncClient() as client:
                # Get recent messages in this conversation
                msg_response = await client.get(
                    f"{GRAPH_API_BASE}/{conversation_id}/messages",
                    params={
                        "access_token": access_token,
                        "fields": "id,from,message,created_time",
                        "limit": 10
                    }
                )
                
                if msg_response.status_code == 200:
                    messages = msg_response.json().get("data", [])
                    
                    # Check if our page has already responded after this user's message
                    user_message_time = message["created_time"]
                    
                    for msg in messages:
                        if (msg["from"]["id"] == page_id and 
                            msg["created_time"] > user_message_time):
                            return False  # We've already responded
                    
                    return True
                    
            return True
            
        except Exception as e:
            logger.error(f"Error checking if should respond: {e}")
            return True
    
    async def _has_replied_to_comment(self, comment_id: str, access_token: str) -> bool:
        """
        Check if we've already replied to a comment.
        """
        try:
            async with httpx.AsyncClient() as client:
                # Get the comment and its replies
                comment_response = await client.get(
                    f"{GRAPH_API_BASE}/{comment_id}",
                    params={
                        "access_token": access_token,
                        "fields": "comments{id,from,created_time}"
                    }
                )
                
                if comment_response.status_code == 200:
                    comment_data = comment_response.json()
                    replies = comment_data.get("comments", {}).get("data", [])
                    
                    # Check if any reply is from our page
                    for reply in replies:
                        if reply.get("from", {}).get("id") == comment_id.split("_")[0]:  # Page ID
                            return True
                    
                    return False
                    
            return False
            
        except Exception as e:
            logger.error(f"Error checking comment replies: {e}")
            return False
    
    async def _get_conversation_context(self, user_id: str, conversation_id: str, page_id: str, access_token: str) -> str:
        """
        Get conversation context for more intelligent responses.
        """
        try:
            # Get conversation session for this user
            session = self.conversation_sessions.get(user_id, [])
            
            # Also get recent messages from Facebook
            async with httpx.AsyncClient() as client:
                msg_response = await client.get(
                    f"{GRAPH_API_BASE}/{conversation_id}/messages",
                    params={
                        "access_token": access_token,
                        "fields": "id,from,message,created_time",
                        "limit": 10
                    }
                )
                
                if msg_response.status_code == 200:
                    messages = msg_response.json().get("data", [])
                    context_messages = []
                    
                    for msg in messages:
                        if msg["from"]["id"] == user_id:
                            context_messages.append(f"User: {msg.get('message', '')}")
                        elif msg["from"]["id"] == page_id:
                            context_messages.append(f"AI: {msg.get('message', '')}")
                    
                    # Combine with session data
                    full_context = session + context_messages[-5:]  # Last 5 messages
                    return " | ".join(full_context)
            
            return " | ".join(session)
            
        except Exception as e:
            logger.error(f"Error getting conversation context: {e}")
            return ""
    
    async def _generate_conversational_response(
        self, 
        user_name: str, 
        message: str, 
        conversation_context: str,
        rule: AutomationRule
    ) -> str:
        """
        Generate a conversational AI response using Groq.
        """
        try:
            # Get the template from the rule
            template = rule.actions.get("message_template", "You're welcome! How can I help you today?")
            
            # Create a conversational prompt
            prompt = f"""
            You are a helpful AI assistant for a business Facebook page. 
            The user {user_name} has sent a message: "{message}"
            
            Previous conversation context: {conversation_context}
            
            Generate a natural, conversational response that:
            1. Addresses the user by name if appropriate
            2. Responds directly to their message
            3. Is helpful and professional
            4. Keeps the conversation engaging
            5. Uses the template as a guide: "{template}"
            6. Stays under 200 characters
            7. Uses appropriate emojis sparingly
            
            Make it feel like a real person is responding, not a bot.
            """
            
            # Generate response using Groq
            ai_result = await groq_service.generate_auto_reply(message, prompt)
            
            if ai_result["success"]:
                response = ai_result["content"]
                
                # Ensure we mention the user if it's their first message
                if not conversation_context or "User:" not in conversation_context:
                    if user_name.lower() not in response.lower():
                        response = f"Hi {user_name}! {response}"
                
                return response
            else:
                # Fallback response
                return f"Hi {user_name}! Thanks for reaching out. How can I help you today? 😊"
                
        except Exception as e:
            logger.error(f"Error generating conversational response: {e}")
            return f"Hi {user_name}! Thanks for your message. How can I assist you? 😊"
    
    async def _send_message_response(self, conversation_id: str, message: str, access_token: str) -> bool:
        """
        Send a message response to the user in the conversation.
        """
        try:
            # Fetch the latest message to get the user ID
            async with httpx.AsyncClient() as client:
                msg_response = await client.get(
                    f"{GRAPH_API_BASE}/{conversation_id}/messages",
                    params={
                        "access_token": access_token,
                        "fields": "id,from,message,created_time",
                        "limit": 1
                    }
                )
                if msg_response.status_code == 200:
                    messages = msg_response.json().get("data", [])
                    if messages:
                        user_id = messages[0]["from"]["id"]
                        # Now send the message using /me/messages
                        send_response = await client.post(
                            f"{GRAPH_API_BASE}/me/messages",
                            params={"access_token": access_token},
                            json={
                                "recipient": {"id": user_id},
                                "message": {"text": message}
                            }
                        )
                        if send_response.status_code == 200:
                            logger.info(f"✅ Message sent successfully to user {user_id}")
                            return True
                        else:
                            logger.error(f"❌ Failed to send message: {send_response.status_code} - {send_response.text}")
                            return False
                logger.error("❌ Could not fetch user ID from conversation.")
                return False
        except Exception as e:
            logger.error(f"❌ Exception while sending message: {e}")
            return False
    
    async def _send_comment_response(self, comment_id: str, message: str, access_token: str) -> bool:
        """
        Send a comment response to a post comment.
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{GRAPH_API_BASE}/{comment_id}/comments",
                    data={
                        "access_token": access_token,
                        "message": message
                    }
                )
                
                if response.status_code == 200:
                    logger.info(f"✅ Comment reply sent successfully to {comment_id}")
                    return True
                else:
                    logger.error(f"❌ Failed to send comment reply: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending comment response: {e}")
            return False
    
    def _update_conversation_session(self, user_id: str, user_message: str, ai_response: str):
        """
        Update the conversation session for this user.
        """
        if user_id not in self.conversation_sessions:
            self.conversation_sessions[user_id] = []
        
        # Add the exchange to the session
        self.conversation_sessions[user_id].extend([
            f"User: {user_message}",
            f"AI: {ai_response}"
        ])
        
        # Keep only the last 10 exchanges to manage memory
        if len(self.conversation_sessions[user_id]) > 20:
            self.conversation_sessions[user_id] = self.conversation_sessions[user_id][-20:]

# Create a singleton instance
facebook_message_auto_reply_service = FacebookMessageAutoReplyService() 