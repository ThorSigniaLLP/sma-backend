from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, JSON, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class PostStatus(str, enum.Enum):
    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PostType(str, enum.Enum):
    TEXT = "text"
    IMAGE = "image"
    VIDEO = "video"
    REEL = "reel"
    LINK = "link"
    CAROUSEL = "carousel"


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"), nullable=False)
    
    # Post content
    content = Column(Text, nullable=False)
    post_type = Column(Enum(PostType), default=PostType.TEXT)
    media_urls = Column(JSON, nullable=True)  # List of image/video URLs
    link_url = Column(String, nullable=True)
    hashtags = Column(JSON, nullable=True)  # List of hashtags
    
    # Scheduling
    status = Column(Enum(PostStatus), default=PostStatus.DRAFT)
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    
    # Platform response
    platform_post_id = Column(String, nullable=True)  # ID from social platform
    platform_response = Column(JSON, nullable=True)  # Full response from platform
    error_message = Column(Text, nullable=True)
    
    # Engagement metrics (updated periodically)
    likes_count = Column(Integer, default=0)
    comments_count = Column(Integer, default=0)
    shares_count = Column(Integer, default=0)
    views_count = Column(Integer, default=0)
    engagement_rate = Column(String, nullable=True)
    
    # Auto-posting configuration
    is_auto_post = Column(Boolean, default=False)
    auto_post_config = Column(JSON, nullable=True)  # Configuration for auto-posting
    
    # Reel thumbnail (for reels only)
    reel_thumbnail_url = Column(String, nullable=True)
    reel_thumbnail_filename = Column(String, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="posts")
    social_account = relationship("SocialAccount", back_populates="posts")
    
    def __repr__(self):
        return f"<Post(id={self.id}, status='{self.status}', platform='{self.social_account.platform if self.social_account else 'Unknown'}')>" 