from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Request as FastAPIRequest
from fastapi.responses import StreamingResponse, HTMLResponse
from typing import Optional, List
import io
import base64
import logging
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload
import os
import json
import tempfile

from ..database import get_db
from ..models.user import User
from ..api.auth import get_current_user
from ..config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/google-drive", tags=["Google Drive"])

# Google Drive API scopes
SCOPES = [
    'https://www.googleapis.com/auth/drive.file',
    'https://www.googleapis.com/auth/drive.readonly'
]

# Get settings
settings = get_settings()

def get_google_drive_service():
    """Get authenticated Google Drive service."""
    creds = None
    
    # First, try to use environment variables for tokens
    if settings.google_drive_access_token and settings.google_drive_refresh_token:
        try:
            creds = Credentials(
                token=settings.google_drive_access_token,
                refresh_token=settings.google_drive_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.google_drive_client_id,
                client_secret=settings.google_drive_client_secret,
                scopes=SCOPES
            )
            logger.info("Using Google Drive credentials from environment variables")
        except Exception as e:
            logger.warning(f"Failed to create credentials from environment variables: {e}")
            creds = None
    
    # If no environment credentials, try token.json file
    if not creds and os.path.exists("token.json"):
        try:
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
            logger.info("Loaded existing credentials from token.json")
        except Exception as e:
            logger.warning(f"Failed to load existing credentials: {e}")
            # Remove invalid token file
            if os.path.exists("token.json"):
                os.remove("token.json")
            creds = None
    
    # If no valid credentials available, let the user log in
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.info("Refreshed expired credentials")
            except Exception as e:
                logger.warning(f"Failed to refresh credentials: {e}")
                creds = None
        
        if not creds:
            # Check if we have environment variables configured
            if not settings.google_drive_client_id or not settings.google_drive_client_secret:
                raise HTTPException(
                    status_code=500,
                    detail="Google Drive credentials not configured. Please set GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET environment variables."
                )
            
            # Create client config from environment variables
            client_config = {
                "installed": {
                    "client_id": settings.google_drive_client_id,
                    "client_secret": settings.google_drive_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                    "redirect_uris": ["urn:ietf:wg:oauth:2.0:oob", "http://localhost:8000/"]
                }
            }
            
            flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
            logger.info("Starting OAuth flow on port 8000...")
            creds = flow.run_local_server(port=8000)
            logger.info("OAuth flow completed successfully")
            
            # Save the credentials for the next run
            try:
                with open("token.json", 'w') as token:
                    token.write(creds.to_json())
                logger.info("Saved credentials to token.json")
            except Exception as e:
                logger.error(f"Failed to save credentials: {e}")
    
    return build('drive', 'v3', credentials=creds)

@router.get("/auth")
async def get_auth_token(current_user: User = Depends(get_current_user)):
    """Get Google Drive authentication token."""
    try:
        logger.info("Attempting to authenticate with Google Drive...")
        service = get_google_drive_service()
        logger.info("Google Drive service created successfully")
        
        # Test the connection
        about = service.about().get(fields="user").execute()
        logger.info("Successfully connected to Google Drive API")
        
        user_email = about.get("user", {}).get("emailAddress", "Unknown")
        logger.info(f"Authenticated as: {user_email}")
        
        # Get the current access token
        access_token = None
        if settings.google_drive_access_token:
            access_token = settings.google_drive_access_token
        elif os.path.exists("token.json"):
            try:
                token_creds = Credentials.from_authorized_user_file("token.json", SCOPES)
                access_token = token_creds.token
            except Exception as token_err:
                logger.warning(f"Unable to load access token from token.json: {token_err}")
        
        return {
            "success": True,
            "message": "Google Drive authenticated successfully",
            "user_email": user_email,
            "access_token": access_token
        }
    except Exception as e:
        logger.error(f"Google Drive authentication error: {e}")
        logger.error(f"Error type: {type(e).__name__}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to authenticate with Google Drive: {str(e)}"
        )

@router.get("/debug")
async def debug_google_drive(current_user: User = Depends(get_current_user)):
    """Debug endpoint to test Google Drive connectivity."""
    try:
        service = get_google_drive_service()
        
        # Test basic connectivity
        about = service.about().get(fields="user,storageQuota").execute()
        user_info = about.get("user", {})
        storage_info = about.get("storageQuota", {})
        
        # Try to list ALL files (without filters)
        all_files = service.files().list(
            pageSize=50,  # Increased to see more files
            fields="files(id, name, mimeType, owners, shared, size)"
        ).execute()
        
        files_list = all_files.get('files', [])
        
        # Categorize files by type
        file_types = {}
        for file in files_list:
            mime_type = file.get('mimeType', 'unknown')
            if mime_type not in file_types:
                file_types[mime_type] = []
            file_types[mime_type].append({
                'name': file.get('name'),
                'id': file.get('id'),
                'size': file.get('size')
            })
        
        # Count image and video files specifically
        image_files = [f for f in files_list if f.get('mimeType', '').startswith('image/')]
        video_files = [f for f in files_list if f.get('mimeType', '').startswith('video/')]
        
        # Show all MIME types found
        mime_types_found = list(file_types.keys())
        
        return {
            "success": True,
            "user": {
                "email": user_info.get("emailAddress"),
                "name": user_info.get("displayName")
            },
            "storage": {
                "total": storage_info.get("limit"),
                "used": storage_info.get("usage"),
                "available": storage_info.get("usageInDrive")
            },
            "total_files_found": len(files_list),
            "image_files_count": len(image_files),
            "video_files_count": len(video_files),
            "file_types_summary": file_types,
            "mime_types_found": mime_types_found,
            "sample_files": files_list[:10],  # Show more sample files
            "message": "Google Drive connection successful"
        }
    except Exception as e:
        logger.error(f"Google Drive debug error: {e}")
        return {
            "success": False,
            "error": str(e),
            "message": "Google Drive connection failed"
        }

@router.get("/files")
async def list_files(
    mime_type: Optional[str] = None,
    current_user: User = Depends(get_current_user)
):
    """List files from Google Drive."""
    try:
        service = get_google_drive_service()
        
        # Build query - be more inclusive for readonly scope
        query_parts = ["trashed=false"]
        
        # Add MIME type filter if specified
        if mime_type:
            if mime_type == 'image/*':
                query_parts.append("(mimeType contains 'image/')")
            elif mime_type == 'video/*':
                query_parts.append("(mimeType contains 'video/')")
            else:
                query_parts.append(f"mimeType contains '{mime_type}'")
        
        # For readonly scope, we can access all files the user has access to
        # Remove the restrictive owner filter that was causing issues
        # query_parts.append("(owners in me or sharedWithMe=true)")
        
        query = " and ".join(query_parts)
        
        logger.info(f"Google Drive query: {query}")
        
        results = service.files().list(
            q=query,
            pageSize=100,  # Increased page size
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime, thumbnailLink, owners, shared, parents, webViewLink)",
            orderBy="modifiedTime desc"  # Show most recent files first
        ).execute()
        
        files = results.get('files', [])
        logger.info(f"Found {len(files)} files in Google Drive")
        
        # Log some file details for debugging
        for i, file in enumerate(files[:5]):  # Log first 5 files
            logger.info(f"File {i+1}: {file.get('name')} ({file.get('mimeType')}) - Owners: {file.get('owners')}")
        
        return {
            "success": True,
            "files": files,
            "query": query,
            "total_files": len(files)
        }
    except Exception as e:
        logger.error(f"Error listing Google Drive files: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list Google Drive files: {str(e)}"
        )

@router.get("/download/{file_id}")
async def download_file(
    file_id: str,
    current_user: User = Depends(get_current_user)
):
    """Download a file from Google Drive."""
    try:
        service = get_google_drive_service()
        
        # Get file metadata
        file_metadata = service.files().get(fileId=file_id).execute()
        
        # Download the file
        request = service.files().get_media(fileId=file_id)
        file_content = io.BytesIO()
        downloader = MediaIoBaseDownload(file_content, request)
        
        done = False
        while done is False:
            status, done = downloader.next_chunk()
        
        file_content.seek(0)
        
        # Return the file content as base64
        file_data = file_content.read()
        file_base64 = base64.b64encode(file_data).decode('utf-8')
        
        return {
            "success": True,
            "fileContent": file_base64,
            "fileName": file_metadata.get('name', 'unknown'),
            "mimeType": file_metadata.get('mimeType', 'application/octet-stream'),
            "size": len(file_data)
        }
    except Exception as e:
        logger.error(f"Error downloading Google Drive file {file_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to download file from Google Drive: {str(e)}"
        )

@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    folder_id: Optional[str] = Form(None),
    current_user: User = Depends(get_current_user)
):
    """Upload a file to Google Drive."""
    try:
        service = get_google_drive_service()
        
        # Read file content
        file_content = await file.read()
        
        # Prepare file metadata
        file_metadata = {
            'name': file.filename,
            'mimeType': file.content_type
        }
        
        if folder_id:
            file_metadata['parents'] = [folder_id]
        
        # Create media upload
        media = MediaIoBaseUpload(
            io.BytesIO(file_content),
            mimetype=file.content_type,
            resumable=True
        )
        
        # Upload the file
        uploaded_file = service.files().create(
            body=file_metadata,
            media_body=media,
            fields='id,name,webViewLink'
        ).execute()
        
        return {
            "success": True,
            "fileId": uploaded_file.get('id'),
            "fileName": uploaded_file.get('name'),
            "webViewLink": uploaded_file.get('webViewLink')
        }
    except Exception as e:
        logger.error(f"Error uploading file to Google Drive: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to upload file to Google Drive: {str(e)}"
        )

@router.get("/folders")
async def list_folders(current_user: User = Depends(get_current_user)):
    """List folders from Google Drive."""
    try:
        service = get_google_drive_service()
        
        results = service.files().list(
            q="mimeType='application/vnd.google-apps.folder' and trashed=false",
            pageSize=50,
            fields="nextPageToken, files(id, name, modifiedTime)"
        ).execute()
        
        folders = results.get('files', [])
        
        return {
            "success": True,
            "folders": folders
        }
    except Exception as e:
        logger.error(f"Error listing Google Drive folders: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list Google Drive folders: {str(e)}"
        )

@router.get("/status")
async def google_drive_status(current_user: User = Depends(get_current_user)):
    """
    Lightweight check: returns authenticated: true/false.
    NEVER triggers the OAuth flow.    
    """
    creds_ok = False
    
    # Check environment variables first
    if settings.google_drive_access_token and settings.google_drive_refresh_token:
        try:
            creds = Credentials(
                token=settings.google_drive_access_token,
                refresh_token=settings.google_drive_refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=settings.google_drive_client_id,
                client_secret=settings.google_drive_client_secret,
                scopes=SCOPES
            )
            creds_ok = creds.valid and not creds.expired
        except Exception:
            pass
    
    # Check token.json file if environment variables not available
    if not creds_ok and os.path.exists("token.json"):
        try:
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
            creds_ok = creds.valid and not creds.expired
        except Exception:
            # corrupted token.json – treat as unauthenticated
            pass

    return {"authenticated": creds_ok}

@router.get("/authorize")
async def get_google_drive_authorize_url(current_user: User = Depends(get_current_user)):
    """
    Get Google OAuth consent URL for popup authentication.
    Returns the URL without triggering a redirect.
    """
    try:
        # Check if already authenticated
        if settings.google_drive_access_token and settings.google_drive_refresh_token:
            try:
                creds = Credentials(
                    token=settings.google_drive_access_token,
                    refresh_token=settings.google_drive_refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=settings.google_drive_client_id,
                    client_secret=settings.google_drive_client_secret,
                    scopes=SCOPES
                )
                if creds and creds.valid and not creds.expired:
                    return {"consent_url": None, "already_authenticated": True}
            except Exception:
                # Invalid environment tokens, continue with auth flow
                pass
        
        if os.path.exists("token.json"):
            try:
                creds = Credentials.from_authorized_user_file("token.json", SCOPES)
                if creds and creds.valid and not creds.expired:
                    return {"consent_url": None, "already_authenticated": True}
            except Exception:
                # Invalid token file, continue with auth flow
                pass

        # Check if we have environment variables configured
        if not settings.google_drive_client_id or not settings.google_drive_client_secret:
            raise HTTPException(
                status_code=500,
                detail="Google Drive credentials not configured. Please set GOOGLE_DRIVE_CLIENT_ID and GOOGLE_DRIVE_CLIENT_SECRET environment variables."
            )

        # Create client config from environment variables
        client_config = {
            "web": {
                "client_id": settings.google_drive_client_id,
                "client_secret": settings.google_drive_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": [f"{settings.backend_base_url}/api/google-drive/oauth2callback"]
            }
        }

        # Create Flow with redirect to our callback
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=f"{settings.backend_base_url}/api/google-drive/oauth2callback"
        )

        # Generate authorization URL
        auth_url, _ = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent'
        )

        return {"consent_url": auth_url}

    except Exception as e:
        logger.error(f"Error getting authorization URL: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get authorization URL: {str(e)}"
        )

@router.get("/oauth2callback", response_model=None)
async def oauth2callback(request: FastAPIRequest):
    """Handle OAuth2 callback and save credentials."""
    try:
        # Get the authorization code from query parameters
        code = request.query_params.get('code')
        if not code:
            return HTMLResponse("""
                <html><body>
                    <script>
                        window.opener.postMessage({error: 'No authorization code received'}, '*');
                        window.close();
                    </script>
                    <p>Authorization failed. You may close this window.</p>
                </body></html>
            """)

        # Create client config
        client_config = {
            "web": {
                "client_id": settings.google_drive_client_id,
                "client_secret": settings.google_drive_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": [f"{settings.backend_base_url}/api/google-drive/oauth2callback"]
            }
        }

        # Create Flow and exchange code for token
        flow = Flow.from_client_config(
            client_config,
            scopes=SCOPES,
            redirect_uri=f"{settings.backend_base_url}/api/google-drive/oauth2callback"
        )

        # Fetch token
        flow.fetch_token(code=code)
        creds = flow.credentials

        # Save credentials
        with open("token.json", 'w') as token:
            token.write(creds.to_json())
        
        logger.info(f"Google Drive credentials saved successfully")

        # Return HTML that closes the popup and notifies parent
        return HTMLResponse("""
            <html><body>
                <script>
                    window.opener.postMessage({success: true}, '*');
                    window.close();
                </script>
                <p>Authentication successful! You may close this window.</p>
            </body></html>
        """)

    except Exception as e:
        logger.error(f"OAuth callback error: {e}")
        return HTMLResponse(f"""
            <html><body>
                <script>
                    window.opener.postMessage({{error: '{str(e)}'}}, '*');
                    window.close();
                </script>
                <p>Authentication failed: {str(e)}. You may close this window.</p>
            </body></html>
        """)

@router.post("/disconnect")
async def disconnect_google_drive(current_user: User = Depends(get_current_user)):
    """Disconnect Google Drive by removing stored credentials."""
    try:
        # Remove token.json file if it exists
        if os.path.exists("token.json"):
            os.remove("token.json")
            logger.info("Removed token.json file")
        
        # Clear environment variables (if they were set)
        # Note: This won't affect the current process, but will be cleared for new processes
        if hasattr(settings, 'google_drive_access_token'):
            settings.google_drive_access_token = None
        if hasattr(settings, 'google_drive_refresh_token'):
            settings.google_drive_refresh_token = None
        
        return {
            "success": True,
            "message": "Google Drive disconnected successfully"
        }
    except Exception as e:
        logger.error(f"Error disconnecting Google Drive: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to disconnect Google Drive: {str(e)}"
        )

@router.get("/token")
async def get_google_drive_token(current_user: User = Depends(get_current_user)):
    """Get a fresh access token for Google Drive API."""
    try:
        service = get_google_drive_service()
        
        # Get the current credentials
        creds = None
        if settings.google_drive_access_token and settings.google_drive_refresh_token:
            try:
                creds = Credentials(
                    token=settings.google_drive_access_token,
                    refresh_token=settings.google_drive_refresh_token,
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=settings.google_drive_client_id,
                    client_secret=settings.google_drive_client_secret,
                    scopes=SCOPES
                )
            except Exception:
                creds = None
        
        if not creds and os.path.exists("token.json"):
            try:
                creds = Credentials.from_authorized_user_file("token.json", SCOPES)
            except Exception:
                creds = None
        
        if not creds:
            raise HTTPException(
                status_code=401,
                detail="Google Drive not authenticated"
            )
        
        # Refresh token if needed
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                # Save updated credentials
                with open("token.json", 'w') as token:
                    token.write(creds.to_json())
            except Exception as e:
                logger.error(f"Failed to refresh token: {e}")
                raise HTTPException(
                    status_code=401,
                    detail="Failed to refresh Google Drive token"
                )
        
        return {
            "success": True,
            "access_token": creds.token,
            "token_type": "Bearer",
            "expires_in": 3600  # Google tokens typically expire in 1 hour
        }
    except Exception as e:
        logger.error(f"Error getting Google Drive token: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get Google Drive token: {str(e)}"
        ) 

@router.get("/test-images")
async def test_image_files(current_user: User = Depends(get_current_user)):
    """Test endpoint to specifically list image and video files."""
    try:
        service = get_google_drive_service()
        
        # Test for image files
        image_query = "trashed=false and (mimeType contains 'image/')"
        image_results = service.files().list(
            q=image_query,
            pageSize=10,
            fields="files(id, name, mimeType, size, modifiedTime)"
        ).execute()
        
        # Test for video files
        video_query = "trashed=false and (mimeType contains 'video/')"
        video_results = service.files().list(
            q=video_query,
            pageSize=10,
            fields="files(id, name, mimeType, size, modifiedTime)"
        ).execute()
        
        return {
            "success": True,
            "image_files": image_results.get('files', []),
            "video_files": video_results.get('files', []),
            "image_count": len(image_results.get('files', [])),
            "video_count": len(video_results.get('files', [])),
            "image_query": image_query,
            "video_query": video_query
        }
    except Exception as e:
        logger.error(f"Error testing image/video files: {e}")
        return {
            "success": False,
            "error": str(e)
        } 