from sqlalchemy import Column, Integer, String, DateTime, Boolean, Text, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum


class BulkComposerStatus(enum.Enum):
    DRAFT = "draft"
    READY = "ready"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    FAILED = "failed"


class BulkComposerContent(Base):
    __tablename__ = "bulk_composer_content"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"), nullable=False)
    
    # Content data
    caption = Column(Text, nullable=False)
    media_file = Column(Text, nullable=True)  # Base64 encoded media
    media_filename = Column(String(255), nullable=True)
    media_generated = Column(Boolean, default=False)  # Whether media was AI-generated
    
    # Schedule data
    scheduled_date = Column(String(10), nullable=False)  # YYYY-MM-DD format
    scheduled_time = Column(String(5), nullable=False)  # HH:MM format
    scheduled_datetime = Column(DateTime(timezone=True), nullable=False)
    schedule_batch_id = Column(String(64), nullable=True, index=True)  # Batch/group identifier for recurring schedules
    
    # Status and tracking
    status = Column(String(20), default=BulkComposerStatus.DRAFT.value)
    facebook_post_id = Column(String(255), nullable=True)  # Facebook's post ID after publishing
    publish_attempts = Column(Integer, default=0)
    last_publish_attempt = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    
    # Metadata
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="bulk_composer_content")
    social_account = relationship("SocialAccount", back_populates="bulk_composer_content")
    
    def __repr__(self):
        return f"<BulkComposerContent(id={self.id}, caption='{self.caption[:50]}...', status={self.status})>" 