from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class SocialAccount(Base):
    __tablename__ = "social_accounts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    platform = Column(String, nullable=False)  # 'google', 'facebook', 'instagram', etc.
    platform_user_id = Column(String, nullable=False)  # The ID from the OAuth provider
    username = Column(String, nullable=True)
    display_name = Column(String, nullable=True)
    access_token = Column(Text, nullable=False)
    refresh_token = Column(Text, nullable=True)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    profile_picture_url = Column(String, nullable=True)
    follower_count = Column(Integer, nullable=True)
    account_type = Column(String, nullable=True)
    is_verified = Column(Boolean, nullable=True)
    platform_data = Column(JSON, nullable=True)  # Store additional OAuth data like email, name
    is_active = Column(Boolean, nullable=True)
    is_connected = Column(Boolean, nullable=True)
    last_sync_at = Column(DateTime(timezone=True), nullable=True)
    connected_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="social_accounts")
    automation_rules = relationship("AutomationRule", back_populates="social_account")
    posts = relationship("Post", back_populates="social_account")
    bulk_composer_content = relationship("BulkComposerContent", back_populates="social_account")
    scheduled_posts = relationship("ScheduledPost", back_populates="social_account")
    single_instagram_posts = relationship("SingleInstagramPost", back_populates="social_account")

    def __repr__(self):
        return f"<SocialAccount(id={self.id}, platform='{self.platform}', user_id={self.user_id})>"