#!/usr/bin/env python3
"""
Quick script to get OTP for a user
"""
import os
import sys
from pathlib import Path

# Add the backend directory to Python path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

# Set up environment variables
from dotenv import load_dotenv
load_dotenv()

from app.database import get_db
from app.models.user import User

def get_user_otp(email):
    db = next(get_db())
    try:
        user = db.query(User).filter(User.email == email).first()
        if user:
            print(f"Email: {user.email}")
            print(f"OTP: {user.otp_code}")
            print(f"Expires: {user.otp_expires_at}")
            print(f"Verified: {user.is_email_verified}")
            return user.otp_code
        else:
            print("User not found")
            return None
    finally:
        db.close()

if __name__ == "__main__":
    email = sys.argv[1] if len(sys.argv) > 1 else "fresh_test@example.com"
    get_user_otp(email)