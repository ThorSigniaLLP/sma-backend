from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ARRAY, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base

class SingleInstagramPost(Base):
    __tablename__ = "single_instagram_posts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    social_account_id = Column(Integer, ForeignKey("social_accounts.id"), nullable=False)
    post_type = Column(String(20), nullable=False)
    media_url = Column(ARRAY(Text), nullable=True)
    caption = Column(Text, nullable=True)
    use_ai_image = Column(Boolean, default=False)
    use_ai_text = Column(Boolean, default=False)
    platform_post_id = Column(String(100), nullable=True)
    status = Column(String(20), default="pending")
    error_message = Column(Text, nullable=True)
    published_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="single_instagram_posts")
    social_account = relationship("SocialAccount", back_populates="single_instagram_posts")
