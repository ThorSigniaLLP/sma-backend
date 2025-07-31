from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum
from datetime import datetime
# from .strategy_plan import StrategyPlan # REMOVE this import to avoid circular import


class FrequencyType(enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class PostType(enum.Enum):
    PHOTO = "photo"
    CAROUSEL = "carousel"
    REEL = "reel"


class ScheduledPost(Base):
    __tablename__ = "scheduled_posts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"), nullable=False)
    
    # Content settings
    prompt = Column(Text, nullable=False) # AI prompt for content generation
    image_url = Column(String, nullable=True) # Cloudinary image URL for single photo posts
    media_urls = Column(JSON, nullable=True) # Array of media URLs for carousel posts
    video_url = Column(String, nullable=True) # Cloudinary video URL for reel posts
    reel_thumbnail_url = Column(String, nullable=True) # Optional thumbnail/cover image URL for reel posts
    post_type = Column(Enum(PostType, name="posttype_new"), nullable=False, default=PostType.PHOTO) # NEW: post type field
    
    # Instagram post ID (media ID) after posting
    post_id = Column(String, nullable=True, index=True)  # Instagram media ID after posting
    
    # Platform
    platform = Column(String(20), nullable=False, default="instagram") # NEW: platform field
    
    # Schedule settings
    post_time = Column(String(5), nullable=False) # HH:MM format
    frequency = Column(Enum(FrequencyType), nullable=False, default=FrequencyType.DAILY)
    scheduled_datetime = Column(DateTime(timezone=True), nullable=True) # NEW: exact scheduled datetime
    
    # Add this field for strategy plan linkage
    strategy_id = Column(Integer, ForeignKey("strategy_plans.id"), nullable=True)
    # strategy_plan = relationship("StrategyPlan", back_populates="scheduled_posts")  # <-- REMOVE this line
    
    # Status
    status = Column(String(20), nullable=False, default="scheduled") # Status field (scheduled, posted, failed)
    is_active = Column(Boolean, default=False)
    last_executed = Column(DateTime(timezone=True), nullable=True)
    next_execution = Column(DateTime(timezone=True), nullable=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="scheduled_posts")
    social_account = relationship("SocialAccount", back_populates="scheduled_posts")
    notifications = relationship("Notification", back_populates="scheduled_post")
    
    def __repr__(self):
        return f"<ScheduledPost(id={self.id}, prompt='{self.prompt[:50]}...', post_type={self.post_type.value}, frequency={self.frequency.value})>"

# Add this at the end of the file to avoid circular import
from app.models.strategy_plan import StrategyPlan
ScheduledPost.strategy_plan = relationship("StrategyPlan", back_populates="scheduled_posts")