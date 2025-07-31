from sqlalchemy import Column, Integer, String, DateTime, func
from app.database import Base

class InstagramAutoReplyLog(Base):
    __tablename__ = "instagram_auto_reply_log"
    id = Column(Integer, primary_key=True)
    comment_id = Column(String, unique=True, index=True, nullable=False)
    instagram_user_id = Column(String, nullable=False)
    replied_at = Column(DateTime(timezone=True), server_default=func.now()) 