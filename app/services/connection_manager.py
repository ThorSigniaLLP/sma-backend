"""
Connection manager service to handle database connections and prevent file descriptor leaks.
"""

import asyncio
import logging
import time
from typing import Dict, Any
from contextlib import asynccontextmanager
from app.database import SessionLocal, engine, get_pool_status, cleanup_connections

logger = logging.getLogger(__name__)

class ConnectionManager:
    """Manages database connections and monitors resource usage."""
    
    def __init__(self):
        self.monitoring = False
        self.cleanup_interval = 300  # 5 minutes
        self.last_cleanup = time.time()
        self.connection_warnings = 0
        
    async def start_monitoring(self):
        """Start connection monitoring."""
        if self.monitoring:
            return
            
        self.monitoring = True
        logger.info("🔍 Starting connection monitoring...")
        
        while self.monitoring:
            try:
                await self._monitor_connections()
                await asyncio.sleep(60)  # Check every minute
            except Exception as e:
                logger.error(f"❌ Error in connection monitoring: {e}")
                await asyncio.sleep(120)  # Wait longer on error
    
    def stop_monitoring(self):
        """Stop connection monitoring."""
        self.monitoring = False
        logger.info("🛑 Connection monitoring stopped")
    
    async def _monitor_connections(self):
        """Monitor database connections and cleanup if needed."""
        try:
            status = get_pool_status()
            
            if "error" in status:
                logger.warning(f"⚠️ Pool status error: {status['error']}")
                return
            
            checked_out = status.get('checked_out', 0)
            overflow = status.get('overflow', 0)
            total = status.get('total_connections', 0)
            
            # Log status every 10 minutes
            if int(time.time()) % 600 == 0:
                logger.info(f"📊 DB Pool: {checked_out} checked out, {overflow} overflow, {total} total")
            
            # Warning threshold
            if checked_out > 4 or overflow > 3:
                self.connection_warnings += 1
                logger.warning(f"⚠️ High connection usage: {status} (warning #{self.connection_warnings})")
                
                # Force cleanup after 3 warnings or if very high usage
                if self.connection_warnings >= 3 or checked_out > 6 or overflow > 5:
                    logger.warning("🧹 Forcing connection cleanup...")
                    await self._force_cleanup()
                    self.connection_warnings = 0
            else:
                # Reset warnings if usage is normal
                if self.connection_warnings > 0:
                    self.connection_warnings = max(0, self.connection_warnings - 1)
            
            # Periodic cleanup
            if time.time() - self.last_cleanup > self.cleanup_interval:
                await self._periodic_cleanup()
                
        except Exception as e:
            logger.error(f"❌ Error monitoring connections: {e}")
    
    async def _force_cleanup(self):
        """Force cleanup of database connections."""
        try:
            logger.info("🧹 Performing forced connection cleanup...")
            
            # Dispose engine to close all connections
            cleanup_success = cleanup_connections()
            
            if cleanup_success:
                logger.info("✅ Forced cleanup completed")
            else:
                logger.error("❌ Forced cleanup failed")
                
            self.last_cleanup = time.time()
            
        except Exception as e:
            logger.error(f"❌ Error in forced cleanup: {e}")
    
    async def _periodic_cleanup(self):
        """Perform periodic maintenance cleanup."""
        try:
            logger.info("🧹 Performing periodic connection cleanup...")
            
            # Get status before cleanup
            before_status = get_pool_status()
            
            # Cleanup connections
            cleanup_connections()
            
            # Get status after cleanup
            after_status = get_pool_status()
            
            logger.info(f"📊 Cleanup: Before {before_status.get('total_connections', 0)} -> After {after_status.get('total_connections', 0)}")
            
            self.last_cleanup = time.time()
            
        except Exception as e:
            logger.error(f"❌ Error in periodic cleanup: {e}")
    
    @asynccontextmanager
    async def get_db_session(self):
        """Context manager for database sessions with automatic cleanup."""
        db = SessionLocal()
        try:
            yield db
            db.commit()
        except Exception as e:
            db.rollback()
            logger.error(f"❌ Database session error: {e}")
            raise e
        finally:
            try:
                db.close()
            except Exception as e:
                logger.error(f"❌ Error closing database session: {e}")

# Global connection manager instance
connection_manager = ConnectionManager()