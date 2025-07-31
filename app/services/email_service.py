import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import os
import logging

logger = logging.getLogger(__name__)

class EmailService:
    def __init__(self):
        self.smtp_server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_username = os.getenv("SMTP_USERNAME")
        self.smtp_password = os.getenv("SMTP_PASSWORD")
        self.from_email = os.getenv("FROM_EMAIL", self.smtp_username)
        
    def send_otp_email(self, to_email: str, otp: str, full_name: str = None) -> bool:
        """Send OTP verification email"""
        try:
            logger.info(f"Attempting to send OTP email to {to_email}")
            logger.info(f"SMTP Server: {self.smtp_server}:{self.smtp_port}")
            logger.info(f"SMTP Username: {self.smtp_username}")
            logger.info(f"From Email: {self.from_email}")
            
            if not self.smtp_username or not self.smtp_password:
                logger.error("SMTP credentials not configured - email cannot be sent")
                return False  # Return False when credentials are missing
            
            # Create message
            message = MIMEMultipart("alternative")
            message["Subject"] = "Email Verification - Your OTP Code"
            message["From"] = self.from_email
            message["To"] = to_email
            
            # Create HTML content
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
                <title>Email Verification</title>
            </head>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
                <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 30px; border-radius: 10px; text-align: center; margin-bottom: 30px;">
                    <h1 style="color: white; margin: 0; font-size: 28px;">Email Verification</h1>
                </div>
                
                <div style="background: #f9f9f9; padding: 30px; border-radius: 10px; margin-bottom: 30px;">
                    <h2 style="color: #333; margin-top: 0;">Hello{' ' + full_name if full_name else ''}!</h2>
                    <p style="font-size: 16px; margin-bottom: 25px;">
                        Thank you for registering with our platform. To complete your registration, please use the verification code below:
                    </p>
                    
                    <div style="background: white; border: 2px dashed #667eea; padding: 20px; text-align: center; border-radius: 8px; margin: 25px 0;">
                        <h3 style="margin: 0; color: #667eea; font-size: 32px; letter-spacing: 8px; font-weight: bold;">
                            {otp}
                        </h3>
                    </div>
                    
                    <p style="font-size: 14px; color: #666; margin-bottom: 0;">
                        This code will expire in <strong>10 minutes</strong>. If you didn't request this verification, please ignore this email.
                    </p>
                </div>
                
                <div style="text-align: center; color: #666; font-size: 12px;">
                    <p>This is an automated message, please do not reply to this email.</p>
                </div>
            </body>
            </html>
            """
            
            # Create plain text version
            text_content = f"""
            Email Verification
            
            Hello{' ' + full_name if full_name else ''}!
            
            Thank you for registering with our platform. To complete your registration, please use the verification code below:
            
            Verification Code: {otp}
            
            This code will expire in 10 minutes. If you didn't request this verification, please ignore this email.
            
            This is an automated message, please do not reply to this email.
            """
            
            # Attach parts
            text_part = MIMEText(text_content, "plain")
            html_part = MIMEText(html_content, "html")
            
            message.attach(text_part)
            message.attach(html_part)
            
            # Send email
            logger.info("Creating SSL context and connecting to SMTP server...")
            context = ssl.create_default_context()
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                logger.info("Starting TLS...")
                server.starttls(context=context)
                logger.info("Logging in to SMTP server...")
                server.login(self.smtp_username, self.smtp_password)
                logger.info("Sending email...")
                server.sendmail(self.from_email, to_email, message.as_string())
            
            logger.info(f"OTP email sent successfully to {to_email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send OTP email to {to_email}: {str(e)}")
            logger.error(f"Exception type: {type(e).__name__}")
            if hasattr(e, 'smtp_code'):
                logger.error(f"SMTP Code: {e.smtp_code}")
            if hasattr(e, 'smtp_error'):
                logger.error(f"SMTP Error: {e.smtp_error}")
            return False

# Global instance
email_service = EmailService()