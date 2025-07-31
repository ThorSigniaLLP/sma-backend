from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, JSON, Enum
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base
import enum


class RuleType(str, enum.Enum):
    AUTO_REPLY = "auto_reply"
    AUTO_POST = "auto_post"
    AUTO_DM = "auto_dm"
    AUTO_FOLLOW = "auto_follow"
    AUTO_LIKE = "auto_like"
    AUTO_COMMENT = "auto_comment"
    AUTO_REPLY_MESSAGE = "AUTO_REPLY_MESSAGE"  # For Facebook message auto-reply

class TriggerType(str, enum.Enum):
    KEYWORD = "KEYWORD"
    MENTION = "MENTION"
    HASHTAG = "HASHTAG"
    TIME_BASED = "TIME_BASED"
    ENGAGEMENT_BASED = "ENGAGEMENT_BASED"
    FOLLOWER_BASED = "FOLLOWER_BASED"


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"), nullable=False)
    
    # Rule configuration
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    rule_type = Column(Enum(RuleType), nullable=False)
    trigger_type = Column(Enum(TriggerType), nullable=False)
    
    # Trigger conditions
    trigger_conditions = Column(JSON, nullable=False)  # Keywords, hashtags, time rules, etc.
    
    # Actions to perform
    actions = Column(JSON, nullable=False)  # Response templates, post content, etc.
    
    # Rule status and limits
    is_active = Column(Boolean, default=True)
    daily_limit = Column(Integer, nullable=True)  # Max executions per day
    daily_count = Column(Integer, default=0)  # Current day executions
    total_executions = Column(Integer, default=0)
    
    # Time constraints
    active_hours_start = Column(String, nullable=True)  # e.g., "09:00"
    active_hours_end = Column(String, nullable=True)    # e.g., "17:00"
    active_days = Column(JSON, nullable=True)           # e.g., ["monday", "tuesday"]
    timezone = Column(String, default="UTC")
    
    # Performance tracking
    success_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    last_execution_at = Column(DateTime(timezone=True), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    last_error_at = Column(DateTime(timezone=True), nullable=True)
    last_error_message = Column(Text, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="automation_rules")
    social_account = relationship("SocialAccount", back_populates="automation_rules")
    
    def __repr__(self):
        return f"<AutomationRule(id={self.id}, name='{self.name}', type='{self.rule_type}', active={self.is_active})>"
    
    def can_execute(self) -> bool:
        """Check if rule can execute based on limits and schedule."""
        if not self.is_active:
            return False
            
        # Check daily limit
        if self.daily_limit and self.daily_count >= self.daily_limit:
            return False
            
        # TODO: Add time-based checks for active_hours and active_days
        
        return True
    
    def increment_execution(self, success: bool = True):
        """Increment execution counters."""
        self.total_executions += 1
        self.daily_count += 1
        self.last_execution_at = func.now()
        
        if success:
            self.success_count += 1
            self.last_success_at = func.now()
        else:
            self.error_count += 1
            self.last_error_at = func.now() 