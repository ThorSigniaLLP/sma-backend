from fastapi import APIRouter, Request, Query, Depends
from app.services.instagram_auto_reply_service import handle_incoming_comment_webhook, handle_incoming_dm_webhook
import logging

logger = logging.getLogger(__name__)
router = APIRouter()

def get_verify_token() -> str:
    """Get the webhook verify token from environment or config."""
    import os
    return os.getenv("INSTAGRAM_WEBHOOK_VERIFY_TOKEN", "your_webhook_verify_token")

@router.get("/webhook/instagram")
async def verify_webhook(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    verify_token: str = Depends(get_verify_token),
):
    """Verify Instagram webhook subscription."""
    logger.info(f"Instagram webhook verification request: mode={hub_mode}, challenge={hub_challenge}")
    
    if hub_mode == "subscribe" and hub_verify_token == verify_token:
        logger.info("✅ Instagram webhook verification successful")
        return int(hub_challenge)
    else:
        logger.error("❌ Instagram webhook verification failed")
        return {"error": "Verification failed"}

@router.post("/webhook/instagram")
async def instagram_webhook(request: Request):
    """Handle incoming Instagram webhooks for comments and DMs."""
    try:
        data = await request.json()
        logger.info(f"📨 Received Instagram webhook: {data}")
        
        # Check if this is a comment webhook
        if "entry" in data:
            for entry in data.get("entry", []):
                for change in entry.get("changes", []):
                    if change.get("field") == "comments":
                        logger.info("🔄 Processing Instagram comment webhook")
                        await handle_incoming_comment_webhook(data)
                        return {"status": "processed"}
                    
                    # Check if this is a DM webhook
                    if change.get("field") == "messages":
                        logger.info("🔄 Processing Instagram DM webhook")
                        result = await handle_incoming_dm_webhook(data)
                        return result
        
        logger.info("📭 No relevant webhook data found")
        return {"status": "ignored"}
        
    except Exception as e:
        logger.error(f"❌ Error processing Instagram webhook: {e}")
        return {"status": "error", "detail": str(e)} 