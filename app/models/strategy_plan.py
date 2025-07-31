from sqlalchemy import Column, Integer, String, Date, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from app.database import Base

class StrategyPlan(Base):
    __tablename__ = "strategy_plans"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    goal = Column(String)
    theme = Column(String)
    start_date = Column(Date)
    time_slot = Column(String) # e.g., "21:00"
    duration = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Relationships
    user = relationship("User", back_populates="strategy_plans")
    scheduled_posts = relationship("ScheduledPost", back_populates="strategy_plan")