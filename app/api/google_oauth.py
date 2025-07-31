from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
import httpx
import secrets
import string
import logging
from typing import Optional

from ..database import get_db
from ..models.user import User
from ..models.social_account import SocialAccount
from ..schemas.social_auth import GoogleOAuthRequest, GoogleOAuthResponse, GoogleUserInfo
from ..schemas.auth import UserResponse
from ..config import get_settings
from ..api.auth import create_access_token, get_password_hash, get_current_user

router = APIRouter(prefix="/auth/google", tags=["Google OAuth"])
settings = get_settings()
logger = logging.getLogger(__name__)

# Google OAuth URLs
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def generate_random_password(length: int = 16) -> str:
    """Generate a random password for OAuth users."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_unique_username(base_username: str, db: Session) -> str:
    """Generate a unique username by appending numbers if needed."""
    username = base_username.lower().replace(" ", "_")
    
    # Remove any non-alphanumeric characters except underscores
    username = ''.join(c for c in username if c.isalnum() or c == '_')
    
    # Ensure it starts with a letter
    if not username[0].isalpha():
        username = "user_" + username
    
    original_username = username
    counter = 1
    
    while db.query(User).filter(User.username == username).first():
        username = f"{original_username}_{counter}"
        counter += 1
    
    return username


async def get_google_user_info(access_token: str) -> GoogleUserInfo:
    """Get user information from Google using the access token."""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"}
        )
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to get user info from Google"
            )
        
        user_data = response.json()
        return GoogleUserInfo(**user_data)


async def exchange_code_for_token(code: str, redirect_uri: str) -> dict:
    """Exchange authorization code for access token."""
    if not settings.google_drive_client_id or not settings.google_drive_client_secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth credentials not configured"
        )
    
    token_data = {
        "client_id": settings.google_drive_client_id,
        "client_secret": settings.google_drive_client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    
    async with httpx.AsyncClient() as client:
        response = await client.post(GOOGLE_TOKEN_URL, data=token_data)
        
        if response.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to exchange code for token"
            )
        
        return response.json()


@router.get("/url")
async def get_google_oauth_url():
    """Get Google OAuth authorization URL."""
    if not settings.google_drive_client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Google OAuth client ID not configured"
        )
    
    # Use the backend URL as redirect URI for OAuth
    redirect_uri = f"{settings.backend_base_url}/api/auth/google/callback"
    
    # Scopes for basic profile info and email
    scopes = [
        "openid",
        "email", 
        "profile"
    ]
    
    auth_url = (
        f"https://accounts.google.com/o/oauth2/v2/auth?"
        f"client_id={settings.google_drive_client_id}&"
        f"redirect_uri={redirect_uri}&"
        f"scope={' '.join(scopes)}&"
        f"response_type=code&"
        f"access_type=offline&"
        f"prompt=consent"
    )
    
    return {
        "auth_url": auth_url,
        "redirect_uri": redirect_uri
    }


@router.post("/callback", response_model=GoogleOAuthResponse)
async def google_oauth_callback(
    oauth_request: GoogleOAuthRequest,
    db: Session = Depends(get_db)
):
    """Handle Google OAuth callback and create/login user."""
    try:
        # Exchange code for access token
        token_response = await exchange_code_for_token(
            oauth_request.code, 
            oauth_request.redirect_uri
        )
        
        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in", 3600)
        
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No access token received from Google"
            )
        
        # Get user info from Google
        google_user = await get_google_user_info(access_token)
        
        # Check if user already exists with this Google account
        social_account = db.query(SocialAccount).filter(
            SocialAccount.platform == "google",
            SocialAccount.platform_user_id == google_user.id
        ).first()
        
        is_new_user = False
        
        if social_account:
            # User exists, update tokens
            user = social_account.user
            social_account.access_token = access_token
            social_account.refresh_token = refresh_token
            social_account.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            social_account.updated_at = datetime.utcnow()
            social_account.platform_data = {
                "email": google_user.email,
                "name": google_user.name,
                "picture": google_user.picture,
                "given_name": google_user.given_name,
                "family_name": google_user.family_name
            }
        else:
            # Check if user exists with this email
            user = db.query(User).filter(User.email == google_user.email).first()
            
            if not user:
                # Create new user
                is_new_user = True
                username = generate_unique_username(google_user.name or google_user.email.split('@')[0], db)
                
                user = User(
                    email=google_user.email,
                    username=username,
                    full_name=google_user.name or "",
                    hashed_password=get_password_hash(generate_random_password()),
                    avatar_url=google_user.picture,
                    is_active=True
                )
                db.add(user)
                db.flush()  # Get the user ID
            
            # Create social account link
            social_account = SocialAccount(
                user_id=user.id,
                platform="google",
                platform_user_id=google_user.id,
                username=google_user.email.split('@')[0],
                display_name=google_user.name,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
                profile_picture_url=google_user.picture,
                platform_data={
                    "email": google_user.email,
                    "name": google_user.name,
                    "picture": google_user.picture,
                    "given_name": google_user.given_name,
                    "family_name": google_user.family_name
                },
                is_active=True,
                is_connected=True
            )
            db.add(social_account)
        
        # Update user's last login
        user.last_login = datetime.utcnow()
        
        # Update avatar if not set or if Google has a newer one
        if google_user.picture and (not user.avatar_url or user.avatar_url != google_user.picture):
            user.avatar_url = google_user.picture
        
        db.commit()
        
        # Create JWT token for our app
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        jwt_token = create_access_token(
            data={"sub": user.email}, 
            expires_delta=access_token_expires
        )
        
        return GoogleOAuthResponse(
            access_token=jwt_token,
            token_type="bearer",
            user=UserResponse(
                id=user.id,
                email=user.email,
                username=user.username,
                full_name=user.full_name,
                is_active=user.is_active,
                created_at=user.created_at
            ),
            is_new_user=is_new_user
        )
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"OAuth authentication failed: {str(e)}"
        )


@router.delete("/disconnect")
async def disconnect_google_account(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Disconnect Google account from user."""
    social_account = db.query(SocialAccount).filter(
        SocialAccount.user_id == current_user.id,
        SocialAccount.platform == "google"
    ).first()
    
    if not social_account:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Google account not connected"
        )
    
    db.delete(social_account)
    db.commit()
    
    return {"message": "Google account disconnected successfully"}


@router.get("/callback")
async def google_oauth_redirect_callback(
    code: Optional[str] = None,
    error: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Handle Google OAuth redirect callback and redirect to frontend."""
    from fastapi.responses import HTMLResponse
    
    logger.info(f"🔍 Google OAuth callback received - Code: {'Present' if code else 'Missing'}, Error: {error}")
    
    if error:
        logger.error(f"❌ Google OAuth error: {error}")
        # Redirect to frontend with error
        response = HTMLResponse(f"""
            <html><body>
                <script>
                    console.log('OAuth error received:', '{error}');
                    const message = {{error: '{error}'}};
                    
                    function sendErrorMessage() {{
                        try {{
                            if (window.opener && !window.opener.closed) {{
                                const origins = ['https://localhost:3000', 'http://localhost:3000', '*'];
                                origins.forEach(origin => {{
                                    try {{
                                        console.log('Posting error to origin:', origin);
                                        window.opener.postMessage(message, origin);
                                    }} catch (e) {{
                                        console.log('Failed to post error to origin:', origin, e);
                                    }}
                                }});
                            }}
                        }} catch (e) {{
                            console.log('Error sending message:', e);
                        }}
                    }}
                    
                    // Send immediately and with delay
                    sendErrorMessage();
                    setTimeout(sendErrorMessage, 1000);
                    
                    setTimeout(() => window.close(), 3000);
                </script>
                <p>Authentication failed: {error}. This window will close automatically.</p>
            </body></html>
        """)
        
        # Remove COOP headers that might interfere
        response.headers["Cross-Origin-Opener-Policy"] = "unsafe-none"
        response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"
        
        return response
    
    if not code:
        logger.error("❌ No authorization code received from Google")
        response = HTMLResponse("""
            <html><body>
                <script>
                    console.log('No authorization code received');
                    const message = {error: 'No authorization code received'};
                    
                    function sendErrorMessage() {
                        try {
                            if (window.opener && !window.opener.closed) {
                                const origins = ['https://localhost:3000', 'http://localhost:3000', '*'];
                                origins.forEach(origin => {
                                    try {
                                        console.log('Posting no-code error to origin:', origin);
                                        window.opener.postMessage(message, origin);
                                    } catch (e) {
                                        console.log('Failed to post error to origin:', origin, e);
                                    }
                                });
                            }
                        } catch (e) {
                            console.log('Error sending message:', e);
                        }
                    }
                    
                    // Send immediately and with delay
                    sendErrorMessage();
                    setTimeout(sendErrorMessage, 1000);
                    
                    setTimeout(() => window.close(), 3000);
                </script>
                <p>Authentication failed. This window will close automatically.</p>
            </body></html>
        """)
        
        # Remove COOP headers that might interfere
        response.headers["Cross-Origin-Opener-Policy"] = "unsafe-none"
        response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"
        
        return response
    
    try:
        logger.info("🔍 Starting OAuth token exchange process...")
        
        # Get the redirect URI that was used for the OAuth request
        redirect_uri = f"{settings.backend_base_url}/api/auth/google/callback"
        logger.info(f"🔍 Using redirect URI: {redirect_uri}")
        
        # Exchange code for access token
        logger.info("🔍 Exchanging code for access token...")
        token_response = await exchange_code_for_token(code, redirect_uri)
        logger.info("✅ Token exchange successful")
        
        access_token = token_response.get("access_token")
        refresh_token = token_response.get("refresh_token")
        expires_in = token_response.get("expires_in", 3600)
        
        if not access_token:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No access token received from Google"
            )
        
        # Get user info from Google
        google_user = await get_google_user_info(access_token)
        
        # Check if user already exists with this Google account
        social_account = db.query(SocialAccount).filter(
            SocialAccount.platform == "google",
            SocialAccount.platform_user_id == google_user.id
        ).first()
        
        is_new_user = False
        
        if social_account:
            # User exists, update tokens
            user = social_account.user
            social_account.access_token = access_token
            social_account.refresh_token = refresh_token
            social_account.token_expires_at = datetime.utcnow() + timedelta(seconds=expires_in)
            social_account.updated_at = datetime.utcnow()
            social_account.platform_data = {
                "email": google_user.email,
                "name": google_user.name,
                "picture": google_user.picture,
                "given_name": google_user.given_name,
                "family_name": google_user.family_name
            }
        else:
            # Check if user exists with this email
            user = db.query(User).filter(User.email == google_user.email).first()
            
            if not user:
                # Create new user
                is_new_user = True
                username = generate_unique_username(google_user.name or google_user.email.split('@')[0], db)
                
                user = User(
                    email=google_user.email,
                    username=username,
                    full_name=google_user.name or "",
                    hashed_password=get_password_hash(generate_random_password()),
                    avatar_url=google_user.picture,
                    is_active=True
                )
                db.add(user)
                db.flush()  # Get the user ID
            
            # Create social account link
            social_account = SocialAccount(
                user_id=user.id,
                platform="google",
                platform_user_id=google_user.id,
                username=google_user.email.split('@')[0],
                display_name=google_user.name,
                access_token=access_token,
                refresh_token=refresh_token,
                token_expires_at=datetime.utcnow() + timedelta(seconds=expires_in),
                profile_picture_url=google_user.picture,
                platform_data={
                    "email": google_user.email,
                    "name": google_user.name,
                    "picture": google_user.picture,
                    "given_name": google_user.given_name,
                    "family_name": google_user.family_name
                },
                is_active=True,
                is_connected=True
            )
            db.add(social_account)
        
        # Update user's last login
        user.last_login = datetime.utcnow()
        
        # Update avatar if not set or if Google has a newer one
        if google_user.picture and (not user.avatar_url or user.avatar_url != google_user.picture):
            user.avatar_url = google_user.picture
        
        db.commit()
        
        # Create JWT token for our app
        access_token_expires = timedelta(minutes=settings.access_token_expire_minutes)
        jwt_token = create_access_token(
            data={"sub": user.email}, 
            expires_delta=access_token_expires
        )
        
        # Send success message to parent window with token and user data
        user_data = {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "full_name": user.full_name,
            "is_active": user.is_active,
            "created_at": user.created_at.isoformat()
        }
        
        import json
        user_data_json = json.dumps(user_data)
        
        # Create response with proper headers for popup communication
        response = HTMLResponse(f"""
            <html><body>
                <script>
                    console.log('🔍 OAuth callback page loaded');
                    console.log('🔍 Window opener available:', !!window.opener);
                    console.log('🔍 Window opener closed:', window.opener ? window.opener.closed : 'N/A');
                    
                    const message = {{
                        success: true,
                        access_token: '{jwt_token}',
                        token_type: 'bearer',
                        user: {user_data_json},
                        is_new_user: {str(is_new_user).lower()}
                    }};
                    
                    console.log('🔍 Preparing to send success message:', message);
                    console.log('🔍 Message size:', JSON.stringify(message).length, 'characters');
                    
                    // Function to send message with retry logic
                    function sendMessage() {{
                        let messageSent = false;
                        let attempts = 0;
                        const maxAttempts = 10;
                        
                        function attemptSend() {{
                            attempts++;
                            console.log(`🔍 Attempt ${{attempts}} to send message`);
                            
                            try {{
                                if (window.opener && !window.opener.closed) {{
                                    console.log('🔍 Opener window is available and open');
                                    
                                    // Try multiple origins for compatibility
                                    const origins = [
                                        'https://localhost:3000',
                                        'http://localhost:3000',
                                        window.location.origin,
                                        '*'
                                    ];
                                    
                                    origins.forEach(origin => {{
                                        try {{
                                            console.log(`🔍 Posting to origin: ${{origin}}`);
                                            window.opener.postMessage(message, origin);
                                            messageSent = true;
                                            console.log(`✅ Message sent to: ${{origin}}`);
                                        }} catch (e) {{
                                            console.log(`❌ Failed to post to origin ${{origin}}:`, e);
                                        }}
                                    }});
                                    
                                    if (messageSent) {{
                                        console.log('✅ OAuth success message sent successfully');
                                        return true;
                                    }}
                                }} else {{
                                    console.log('❌ No opener window available or opener is closed');
                                    console.log('🔍 window.opener:', !!window.opener);
                                    console.log('🔍 window.opener.closed:', window.opener ? window.opener.closed : 'N/A');
                                }}
                            }} catch (e) {{
                                console.log(`❌ Error in attempt ${{attempts}}:`, e);
                            }}
                            
                            // Retry if not successful and haven't exceeded max attempts
                            if (!messageSent && attempts < maxAttempts) {{
                                console.log(`🔄 Retrying in 1000ms (attempt ${{attempts + 1}}/${{maxAttempts}})`);
                                setTimeout(attemptSend, 1000);
                            }} else if (!messageSent) {{
                                console.log('❌ Failed to send message after all attempts');
                                // Try localStorage as fallback
                                try {{
                                    console.log('🔍 Trying localStorage fallback');
                                    localStorage.setItem('oauth_result', JSON.stringify(message));
                                    console.log('✅ Stored OAuth result in localStorage');
                                }} catch (e) {{
                                    console.log('❌ localStorage fallback failed:', e);
                                }}
                            }}
                            
                            return messageSent;
                        }}
                        
                        return attemptSend();
                    }}
                    
                    // Start sending message immediately
                    sendMessage();
                    
                    // Also try sending after a short delay in case the parent isn't ready
                    setTimeout(() => {{
                        console.log('🔍 Delayed message send attempt');
                        sendMessage();
                    }}, 1000);
                    
                    // Close window after sufficient delay to ensure message delivery
                    setTimeout(() => {{
                        console.log('🔍 Closing popup window');
                        window.close();
                    }}, 5000);
                </script>
                <p>Authentication successful! This window will close automatically.</p>
                <p>If this window doesn't close, you can close it manually.</p>
            </body></html>
        """)
        
        # Remove COOP headers that might interfere with popup communication
        response.headers["Cross-Origin-Opener-Policy"] = "unsafe-none"
        response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"
        
        return response
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        logger.error(f"OAuth callback error: {str(e)}")
        logger.error(f"Full traceback: {error_details}")
        
        db.rollback()
        response = HTMLResponse(f"""
            <html><body>
                <script>
                    const message = {{error: 'OAuth authentication failed: {str(e)}'}};
                    
                    function sendErrorMessage() {{
                        try {{
                            if (window.opener && !window.opener.closed) {{
                                const origins = ['https://localhost:3000', 'http://localhost:3000', '*'];
                                origins.forEach(origin => {{
                                    try {{
                                        window.opener.postMessage(message, origin);
                                    }} catch (e) {{
                                        console.log('Failed to post error to origin:', origin, e);
                                    }}
                                }});
                            }}
                        }} catch (e) {{
                            console.log('Error sending message:', e);
                        }}
                    }}
                    
                    // Send immediately and with delay
                    sendErrorMessage();
                    setTimeout(sendErrorMessage, 1000);
                    
                    setTimeout(() => window.close(), 3000);
                </script>
                <p>Authentication failed: {str(e)}. This window will close automatically.</p>
            </body></html>
        """)
        
        # Remove COOP headers that might interfere
        response.headers["Cross-Origin-Opener-Policy"] = "unsafe-none"
        response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"
        
        return response