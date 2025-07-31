from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import get_settings

settings = get_settings()

# Create database engine with Windows-optimized pool settings
if settings.database_url.startswith("postgresql"):
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=False,  # Disable SQL logging to reduce noise
        pool_size=3,          # Reduced pool size for Windows compatibility
        max_overflow=5,       # Reduced overflow (total max = 8 connections)
        pool_timeout=5,       # Fast timeout to fail quickly
        pool_recycle=300,     # Recycle connections every 5 minutes
        pool_reset_on_return='commit',  # Reset connections on return
        connect_args={
            "options": "-c timezone=utc",  # Set timezone to UTC
            "connect_timeout": 3  # Very fast connection timeout
        }
    )
else:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        echo=settings.debug
    )

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Create base class for models
Base = declarative_base()


# Dependency to get database session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Initialize database (for Alembic compatibility)
def init_db():
    """Initialize database - imports all models to ensure they're registered with SQLAlchemy"""
    try:
        # Import all models to ensure they're registered with Base.metadata
        from app.models import user, automation_rule, post, social_account
        print("✅ Database models registered successfully")
        return True
    except Exception as e:
        print(f"❌ Database model registration error: {e}")
        return False


# Verify database connection
def verify_db_connection():
    """Verify database connection without creating tables"""
    try:
        with engine.connect() as connection:
            result = connection.execute("SELECT 1")
            result.fetchone()  # Actually fetch the result
        print("✅ Database connection verified")
        return True
    except Exception as e:
        print(f"❌ Database connection error: {e}")
        return False


# Monitor database connection pool
def get_pool_status():
    """Get current database connection pool status for monitoring"""
    try:
        pool = engine.pool
        return {
            "pool_size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow(),
            "total_connections": pool.checkedout() + pool.checkedin()
        }
    except Exception as e:
        return {"error": str(e)}

# Force cleanup of database connections
def cleanup_connections():
    """Force cleanup of database connections"""
    try:
        engine.dispose()
        print("✅ Database connections cleaned up")
        return True
    except Exception as e:
        print(f"❌ Error cleaning up connections: {e}")
        return False

# Context manager for database sessions
from contextlib import contextmanager

@contextmanager
def get_db_session():
    """Context manager for database sessions with automatic cleanup"""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        raise e
    finally:
        db.close() 