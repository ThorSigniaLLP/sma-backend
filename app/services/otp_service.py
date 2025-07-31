import random
import string
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from ..models.user import User
from .email_service import email_service
import logging

logger = logging.getLogger(__name__)

class OTPService:
    def __init__(self):
        self.otp_length = 6
        self.otp_expiry_minutes = 30  # Increased from 10 to 30 minutes
    
    def generate_otp(self) -> str:
        """Generate a random 6-digit OTP"""
        return ''.join(random.choices(string.digits, k=self.otp_length))
    
    def send_otp(self, email: str, db: Session) -> bool:
        """Generate and send OTP to user's email"""
        try:
            # Check if user exists
            user = db.query(User).filter(User.email == email).first()
            if not user:
                logger.warning(f"Attempted to send OTP to non-existent user: {email}")
                return False
            
            # Generate new OTP
            otp = self.generate_otp()
            expires_at = datetime.now(timezone.utc) + timedelta(minutes=self.otp_expiry_minutes)
            
            logger.info(f"Generated OTP {otp} for {email}, expires at {expires_at} UTC")
            
            # Update user with OTP
            user.otp_code = otp
            user.otp_expires_at = expires_at
            db.commit()
            
            # Send email
            logger.info(f"Calling email service to send OTP to {email}")
            success = email_service.send_otp_email(
                to_email=email,
                otp=otp,
                full_name=user.full_name
            )
            
            if success:
                logger.info(f"OTP sent successfully to {email}")
                return True
            else:
                logger.error(f"Failed to send OTP email to {email}")
                # Clear the OTP from database if email failed
                user.otp_code = None
                user.otp_expires_at = None
                db.commit()
                return False
                
        except Exception as e:
            logger.error(f"Error sending OTP to {email}: {str(e)}")
            db.rollback()
            return False
    
    def verify_otp(self, email: str, otp: str, db: Session) -> bool:
        """Verify OTP for a user"""
        try:
            user = db.query(User).filter(User.email == email).first()
            if not user:
                logger.warning(f"Attempted to verify OTP for non-existent user: {email}")
                return False
            
            # Check if OTP exists and hasn't expired
            if not user.otp_code or not user.otp_expires_at:
                logger.warning(f"No OTP found for user: {email}")
                return False
            
            current_time = datetime.now(timezone.utc)
            
            # Ensure both times are timezone-aware and in UTC for comparison
            expires_at_utc = user.otp_expires_at
            if expires_at_utc.tzinfo is None:
                # If stored time is naive, assume it's UTC
                expires_at_utc = expires_at_utc.replace(tzinfo=timezone.utc)
            elif expires_at_utc.tzinfo != timezone.utc:
                # Convert to UTC if it's in a different timezone
                expires_at_utc = expires_at_utc.astimezone(timezone.utc)
            
            logger.info(f"Verifying OTP for {email}: current_time={current_time}, expires_at_utc={expires_at_utc}")
            
            if current_time > expires_at_utc:
                logger.warning(f"OTP expired for user: {email}")
                # Clear expired OTP
                user.otp_code = None
                user.otp_expires_at = None
                db.commit()
                return False
            
            # Verify OTP
            if user.otp_code == otp:
                # Mark email as verified and clear OTP
                user.is_email_verified = True
                user.otp_code = None
                user.otp_expires_at = None
                db.commit()
                logger.info(f"OTP verified successfully for user: {email}")
                return True
            else:
                logger.warning(f"Invalid OTP provided for user: {email}. Expected: {user.otp_code}, Got: {otp}")
                return False
                
        except Exception as e:
            logger.error(f"Error verifying OTP for {email}: {str(e)}")
            db.rollback()
            return False
    
    def resend_otp(self, email: str, db: Session) -> bool:
        """Resend OTP to user's email"""
        return self.send_otp(email, db)

# Global instance
otp_service = OTPService()