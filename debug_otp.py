#!/usr/bin/env python3
"""
Debug script to investigate OTP verification issues
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timezone

# Add the backend directory to Python path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

# Set up environment variables
from dotenv import load_dotenv
load_dotenv()

from app.database import get_db
from app.services.otp_service import otp_service
from app.models.user import User
from sqlalchemy.orm import Session

def debug_otp_for_user():
    print("🔍 OTP Debug Tool")
    print("=" * 50)
    
    # Get database session
    db = next(get_db())
    
    try:
        # Get user email from input
        email = input("Enter the email address to debug: ").strip()
        if not email:
            print("❌ No email provided")
            return
        
        # Find user in database
        user = db.query(User).filter(User.email == email).first()
        if not user:
            print(f"❌ User with email {email} not found")
            return
        
        print(f"✅ Found user: {user.full_name} ({user.email})")
        print(f"   Email verified: {user.is_email_verified}")
        print(f"   Current OTP: {user.otp_code}")
        print(f"   OTP expires at: {user.otp_expires_at}")
        
        if user.otp_code and user.otp_expires_at:
            current_time = datetime.now(timezone.utc)
            print(f"   Current UTC time: {current_time}")
            
            time_diff = user.otp_expires_at - current_time
            print(f"   Time until expiry: {time_diff}")
            
            if current_time > user.otp_expires_at:
                print("   ❌ OTP is EXPIRED")
            else:
                print("   ✅ OTP is still VALID")
                print(f"   ⏰ Time remaining: {time_diff.total_seconds():.0f} seconds")
        
        # Test OTP verification
        if user.otp_code:
            print(f"\n🧪 Testing OTP verification with correct OTP: {user.otp_code}")
            result = otp_service.verify_otp(email, user.otp_code, db)
            print(f"   Verification result: {result}")
            
            if not result:
                print("   ❌ Verification failed even with correct OTP!")
                # Refresh user data
                db.refresh(user)
                print(f"   Updated OTP: {user.otp_code}")
                print(f"   Updated expires at: {user.otp_expires_at}")
        
        # Option to send new OTP
        send_new = input("\nDo you want to send a new OTP? (y/n): ").strip().lower()
        if send_new == 'y':
            print("📧 Sending new OTP...")
            success = otp_service.send_otp(email, db)
            if success:
                print("✅ New OTP sent successfully!")
                # Refresh user data
                db.refresh(user)
                print(f"   New OTP: {user.otp_code}")
                print(f"   New expires at: {user.otp_expires_at}")
                
                current_time = datetime.now(timezone.utc)
                time_diff = user.otp_expires_at - current_time
                print(f"   Time until expiry: {time_diff.total_seconds():.0f} seconds ({time_diff.total_seconds()/60:.1f} minutes)")
            else:
                print("❌ Failed to send new OTP")
        
    except Exception as e:
        print(f"❌ Error: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        db.close()

if __name__ == "__main__":
    debug_otp_for_user()