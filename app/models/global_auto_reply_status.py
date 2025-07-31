from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.database import Base


class GlobalAutoReplyStatus(Base):
    __tablename__ = "global_auto_reply_status"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)  # NEW: Track by user
    instagram_user_id = Column(String(255), nullable=False, index=True)
    enabled = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # Relationships
    user = relationship("User", back_populates="global_auto_reply_status")
    
    @classmethod
    def is_enabled(cls, user_id: int, instagram_user_id: str, db):
        status = db.query(cls).filter_by(user_id=user_id, instagram_user_id=instagram_user_id).first()
        print(f"[DEBUG] is_enabled: user={user_id}, ig={instagram_user_id}, found={bool(status)}, enabled={getattr(status, 'enabled', None)}")
        return status.enabled if status else False
    
    @classmethod
    def set_enabled(cls, user_id: int, instagram_user_id: str, enabled: bool, db):
        status = db.query(cls).filter_by(user_id=user_id, instagram_user_id=instagram_user_id).first()
        if status:
            status.enabled = enabled
        else:
            status = cls(user_id=user_id, instagram_user_id=instagram_user_id, enabled=enabled)
            db.add(status)
        db.commit()
        print(f"[DEBUG] set_enabled: user={user_id}, ig={instagram_user_id}, enabled={enabled}")
        return status 