from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime


class GoogleUserInfo(BaseModel):
    id: str
    email: str
    name: str
    picture: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None


class GoogleOAuthRequest(BaseModel):
    code: str
    redirect_uri: str


class SocialAccountResponse(BaseModel):
    id: int
    provider: str
    provider_id: str
    email: Optional[str]
    name: Optional[str]
    avatar_url: Optional[str]
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class GoogleOAuthResponse(BaseModel):
    access_token: str
    token_type: str
    user: "UserResponse"
    is_new_user: bool

    class Config:
        from_attributes = True


# Import here to avoid circular imports
from .auth import UserResponse
GoogleOAuthResponse.model_rebuild()