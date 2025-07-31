from sqlalchemy import Column, String, Text, Boolean, DateTime, ForeignKey, Enum, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum
from app.database import Base

class NotificationType(enum.Enum):
    PRE_POSTING = "pre_posting"
    SUCCESS = "success"
    FAILURE = "failure"

class NotificationPlatform(enum.Enum):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    post_id = Column(Integer, ForeignKey("scheduled_posts.id"), nullable=True)
    type = Column(Enum(NotificationType), nullable=False)
    platform = Column(Enum(NotificationPlatform), nullable=False)
    strategy_name = Column(String(255), nullable=True)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    scheduled_time = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)

    # Relationships
    user = relationship("User", back_populates="notifications")
    scheduled_post = relationship("ScheduledPost", back_populates="notifications")

class NotificationPreferences(Base):
    __tablename__ = "notification_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    browser_notifications_enabled = Column(Boolean, default=True, nullable=False)
    pre_posting_enabled = Column(Boolean, default=True, nullable=False)
    success_enabled = Column(Boolean, default=True, nullable=False)
    failure_enabled = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="notification_preferences")