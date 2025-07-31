from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from ..config import get_settings
import logging

logger = logging.getLogger(__name__)

from ..database import get_db
from ..models.user import User
from ..schemas.auth import UserCreate, UserLogin, Token, UserResponse, OTPRequest, OTPVerify, OTPResponse
from ..services.otp_service import otp_service

router = APIRouter(prefix="/auth", tags=["authentication"])
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# JWT settings
settings = get_settings()
SECRET_KEY = settings.secret_key
ALGORITHM = settings.algorithm
ACCESS_TOKEN_EXPIRE_MINUTES = settings.access_token_expire_minutes

def verify_password(plain_password, hashed_password):
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password):
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        # Decode and validate JWT token
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        exp: int = payload.get("exp")
        
        if email is None:
            raise credentials_exception
            
        # Check if token is expired
        if exp is None or datetime.utcnow().timestamp() > exp:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
            
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except JWTError as e:
        logger.error(f"JWT validation error: {e}")
        raise credentials_exception
    
    # Get user from database
    user = db.query(User).filter(User.email == email).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if user is active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is inactive",
        )
        
    return user

async def get_user_from_token(token: str, db: Session):
    """Get user from JWT token for WebSocket authentication"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        exp: int = payload.get("exp")
        
        if email is None:
            return None
            
        # Check if token is expired
        if exp is None or datetime.utcnow().timestamp() > exp:
            logger.warning(f"Expired token for WebSocket authentication: {email}")
            return None
        
        user = db.query(User).filter(User.email == email).first()
        if user and not user.is_active:
            logger.warning(f"Inactive user attempted WebSocket connection: {email}")
            return None
            
        return user
    except jwt.ExpiredSignatureError:
        logger.warning("Expired JWT token for WebSocket authentication")
        return None
    except JWTError as e:
        logger.error(f"JWT validation error for WebSocket: {e}")
        return None

@router.post("/register", response_model=dict)
def register(user: UserCreate, db: Session = Depends(get_db)):
    # Check if user already exists
    db_user = db.query(User).filter(User.email == user.email).first()
    if db_user:
        raise HTTPException(
            status_code=400,
            detail="Email already registered"
        )
    
    # Check if username already exists
    db_username = db.query(User).filter(User.username == user.username).first()
    if db_username:
        raise HTTPException(
            status_code=400,
            detail="Username already taken"
        )
    
    # Create new user (inactive until email verification)
    hashed_password = get_password_hash(user.password)
    db_user = User(
        email=user.email,
        username=user.username,
        full_name=user.full_name,
        hashed_password=hashed_password,
        is_active=False,  # User inactive until email verification
        is_email_verified=False
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    # Send OTP for email verification
    otp_sent = otp_service.send_otp(user.email, db)
    if not otp_sent:
        # If OTP sending fails, still allow registration but inform user
        return {
            "message": "Registration successful! However, we couldn't send the verification email. Please try requesting OTP again.",
            "email": user.email,
            "otp_sent": False
        }
    
    return {
        "message": "Registration successful! Please check your email for the verification code.",
        "email": user.email,
        "otp_sent": True
    }

@router.post("/login", response_model=Token)
def login(user: UserLogin, db: Session = Depends(get_db)):
    # Authenticate user
    db_user = db.query(User).filter(User.email == user.email).first()
    if not db_user or not verify_password(user.password, db_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Check if email is verified
    if not db_user.is_email_verified:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email not verified. Please verify your email before logging in.",
        )
    
    # Update last login
    db_user.last_login = datetime.utcnow()
    db.commit()
    
    # Create access token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": db_user.email}, expires_delta=access_token_expires
    )
    
    return Token(
        access_token=access_token,
        token_type="bearer",
        user=UserResponse(
            id=db_user.id,
            email=db_user.email,
            username=db_user.username,
            full_name=db_user.full_name,
            is_active=db_user.is_active,
            created_at=db_user.created_at
        )
    )

@router.post("/send-otp", response_model=OTPResponse)
def send_otp(request: OTPRequest, db: Session = Depends(get_db)):
    """Send OTP to user's email for verification"""
    # Check if user exists
    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )
    
    # Check if already verified
    if user.is_email_verified:
        raise HTTPException(
            status_code=400,
            detail="Email already verified"
        )
    
    # Send OTP
    success = otp_service.send_otp(request.email, db)
    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed to send OTP. Please try again."
        )
    
    return OTPResponse(
        message="OTP sent successfully to your email",
        expires_in=1800  # 30 minutes in seconds
    )


@router.post("/verify-otp", response_model=dict)
def verify_otp(request: OTPVerify, db: Session = Depends(get_db)):
    """Verify OTP and activate user account"""
    success = otp_service.verify_otp(request.email, request.otp, db)
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Invalid or expired OTP"
        )
    
    # Activate user account
    user = db.query(User).filter(User.email == request.email).first()
    if user:
        user.is_active = True
        db.commit()
    
    return {
        "message": "Email verified successfully! You can now log in.",
        "verified": True
    }


@router.post("/resend-otp", response_model=OTPResponse)
def resend_otp(request: OTPRequest, db: Session = Depends(get_db)):
    """Resend OTP to user's email"""
    # Check if user exists
    user = db.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(
            status_code=404,
            detail="User not found"
        )
    
    # Check if already verified
    if user.is_email_verified:
        raise HTTPException(
            status_code=400,
            detail="Email already verified"
        )
    
    # Resend OTP
    success = otp_service.resend_otp(request.email, db)
    if not success:
        raise HTTPException(
            status_code=500,
            detail="Failed to resend OTP. Please try again."
        )


@router.post("/test-email")
def test_email(request: OTPRequest):
    """Test email sending functionality"""
    from ..services.email_service import email_service
    
    try:
        # Test with a simple OTP
        test_otp = "123456"
        success = email_service.send_otp_email(
            to_email=request.email,
            otp=test_otp,
            full_name="Test User"
        )
        
        return {
            "success": success,
            "message": "Test email sent successfully" if success else "Failed to send test email",
            "email": request.email,
            "smtp_server": email_service.smtp_server,
            "smtp_port": email_service.smtp_port,
            "smtp_username": email_service.smtp_username,
            "from_email": email_service.from_email
        }
    except Exception as e:
        logger.error(f"Test email error: {str(e)}")
        return {
            "success": False,
            "message": f"Error: {str(e)}",
            "email": request.email
        }
    
    return OTPResponse(
        message="OTP resent successfully to your email",
        expires_in=1800  # 30 minutes in seconds
    )


@router.get("/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return UserResponse(
        id=current_user.id,
        email=current_user.email,
        username=current_user.username,
        full_name=current_user.full_name,
        is_active=current_user.is_active,
        created_at=current_user.created_at
    ) 