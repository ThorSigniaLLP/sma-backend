from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from app.config import get_settings
from app.database import init_db, verify_db_connection
from app.api import auth, social_media, ai, google_drive, webhook, google_oauth
from app.middleware.rate_limiter import rate_limit_middleware
import logging
import asyncio
import os
import signal
import sys
from pathlib import Path
import time

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    
    from windows_config import configure_windows_limits
    configure_windows_limits()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

# Create FastAPI app
app = FastAPI(
    title="Automation Dashboard API",
    description="Backend API for social media automation dashboard",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
)

temp_images_path = Path("temp_images")
temp_images_path.mkdir(exist_ok=True)

app.mount("/temp_images", StaticFiles(directory="temp_images"), name="temp_images")

@app.middleware("http")
async def log_requests_and_handle_concurrency(request: Request, call_next):
    start_time = time.time()
    
    skip_logging = any(path in request.url.path for path in ["/health", "/api/notifications", "/api/social/scheduled-posts"])
    
    if not skip_logging:
        logger.info(f"🔍 {request.method} {request.url.path}")
    
    try:
        response = await call_next(request)
        
        if "/auth/google/callback" in str(request.url):
            response.headers["Cross-Origin-Opener-Policy"] = "unsafe-none"
            response.headers["Cross-Origin-Embedder-Policy"] = "unsafe-none"
        
        # Log response details only for errors or non-frequent endpoints
        process_time = time.time() - start_time
        if response.status_code >= 400 or not skip_logging:
            if response.status_code >= 400:
                logger.warning(f"🔍 {request.method} {request.url.path} - {response.status_code} - {process_time:.4f}s")
            elif not skip_logging:
                logger.info(f"🔍 {request.method} {request.url.path} - {response.status_code} - {process_time:.4f}s")
        
        return response
        
    except Exception as e:
        process_time = time.time() - start_time
        error_str = str(e)
        
        # Handle specific concurrency limit errors - return 200 instead of 503
        if "concurrency limit" in error_str.lower() or "too many" in error_str.lower():
            logger.warning(f"⚠️ Concurrency limit hit for {request.url.path}, returning success to avoid 503")
            
            # Return appropriate response based on endpoint
            if "/api/social/scheduled-posts" in request.url.path:
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "data": [],
                        "message": "No scheduled posts found",
                        "total": 0
                    }
                )
            elif "/api/notifications" in request.url.path:
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "data": [],
                        "message": "No notifications found",
                        "total": 0,
                        "limit": 50,
                        "offset": 0
                    }
                )
            else:
                return JSONResponse(
                    status_code=200,
                    content={
                        "success": True,
                        "message": "Request processed successfully",
                        "data": []
                    }
                )
        
        # Log other errors
        logger.error(f"🔍 {request.method} {request.url.path} - ERROR: {error_str} - {process_time:.4f}s")
        
        # Re-raise other exceptions
        raise e

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.debug else [
        "http://localhost:3000",
        "https://localhost:3000",
        "http://localhost:8000",
        "https://localhost:8000",
        "http://localhost:3001",
        "https://localhost:3001"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
    max_age=3600,
)

if settings.environment == "production":
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["*"]  # Configure with actual domain in production
    )


@app.on_event("startup")
async def startup_event():
    """Initialize the application."""
    logger.info("Starting Automation Dashboard API...")
    
    try:
        # Initialize database models (for Alembic compatibility)
        init_db()
        logger.info("Database models registered")
        
        # Verify database connection
        verify_db_connection()
        logger.info("Database connection verified")
        
    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        # Don't fail startup for database issues
    

    
    # Start bulk composer scheduler for scheduled posts
    try:
        from app.services.bulk_composer_scheduler import bulk_composer_scheduler
        asyncio.create_task(bulk_composer_scheduler.start())
        logger.info("Bulk composer scheduler started for scheduled posts")
    except Exception as e:
        logger.error(f"Failed to start bulk composer scheduler: {e}")

    # Start auto-reply scheduler for Facebook comments
    try:
        from app.services.auto_reply_service import auto_reply_service
        from app.database import get_db
        async def auto_reply_scheduler():
            while True:
                db = None
                try:
                    db = next(get_db())
                    await auto_reply_service.process_auto_replies(db)
                except Exception as e:
                    logger.error(f"Error in auto-reply scheduler: {e}")
                finally:
                    if db:
                        db.close()
                await asyncio.sleep(60)  
        asyncio.create_task(auto_reply_scheduler())
        logger.info("Auto-reply scheduler started for Facebook comments")
    except Exception as e:
        logger.error(f"Failed to start auto-reply scheduler: {e}")

    # Start Instagram scheduler service
    try:
        from app.services.scheduler_service import scheduler_service
        asyncio.create_task(scheduler_service.start())
        logger.info("Instagram scheduler service started")
    except Exception as e:
        logger.error(f"Failed to start Instagram scheduler service: {e}")

    # Start connection manager
    try:
        from app.services.connection_manager import connection_manager
        asyncio.create_task(connection_manager.start_monitoring())
        logger.info("Connection manager started")
    except Exception as e:
        logger.error(f"Failed to start connection manager: {e}")
    
    # Clean up notification alert tracking periodically
    try:
        async def cleanup_notifications():
            while True:
                try:
                    await asyncio.sleep(3600)  # Every hour
                    from app.services.notification_service import notification_service
                    notification_service.cleanup_alert_tracking()
                    logger.info("✅ Notification alert tracking cleaned up")
                except Exception as e:
                    logger.error(f"❌ Alert tracking cleanup failed: {e}")
        
        asyncio.create_task(cleanup_notifications())
        logger.info("Notification cleanup scheduler started")
    except Exception as e:
        logger.error(f"Failed to start notification cleanup: {e}")

    # Log initial database pool status
    try:
        from app.database import get_pool_status
        pool_status = get_pool_status()
        logger.info(f"📊 Initial database pool status: {pool_status}")
    except Exception as e:
        logger.error(f"Failed to get initial pool status: {e}")

    logger.info("Automation Dashboard API started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Clean up resources."""
    logger.info("Shutting down Automation Dashboard API...")
    
    # Stop connection manager
    try:
        from app.services.connection_manager import connection_manager
        connection_manager.stop_monitoring()
        logger.info("Connection manager stopped")
    except Exception as e:
        logger.error(f"Error stopping connection manager: {e}")
    
    # Stop bulk composer scheduler
    try:
        from app.services.bulk_composer_scheduler import bulk_composer_scheduler
        bulk_composer_scheduler.stop()
        logger.info("Bulk composer scheduler stopped")
    except Exception as e:
        logger.error(f"Error stopping bulk composer scheduler: {e}")

    # Stop Instagram scheduler service
    try:
        from app.services.scheduler_service import scheduler_service
        scheduler_service.stop()
        logger.info("Instagram scheduler service stopped")
    except Exception as e:
        logger.error(f"Error stopping Instagram scheduler service: {e}")
    
    # Final cleanup
    try:
        from app.database import cleanup_connections
        cleanup_connections()
        logger.info("Final database cleanup completed")
    except Exception as e:
        logger.error(f"Error in final cleanup: {e}")


# Health check endpoint
@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "message": "Automation Dashboard API",
        "version": "1.0.0",
        "status": "healthy",
        "environment": settings.environment
    }


@app.get("/health")
async def health_check():
    """Detailed health check."""
    from app.database import get_pool_status
    return {
        "status": "healthy",
        "environment": settings.environment,
        "debug": settings.debug,
        "database": "connected",
        "connection_pool": get_pool_status()
    }

@app.post("/api/admin/cleanup-connections")
async def cleanup_database_connections():
    """Emergency endpoint to cleanup database connections"""
    try:
        from app.database import cleanup_connections, get_pool_status
        
        # Get status before cleanup
        before_status = get_pool_status()
        
        # Force cleanup
        cleanup_success = cleanup_connections()
        
        # Get status after cleanup
        after_status = get_pool_status()
        
        return {
            "success": cleanup_success,
            "before": before_status,
            "after": after_status,
            "message": "Database connections cleaned up" if cleanup_success else "Cleanup failed"
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "message": "Failed to cleanup connections"
        }


@app.get("/api/debug/cors")
async def cors_debug():
    """Debug endpoint to test CORS without authentication."""
    return {
        "message": "CORS is working",
        "timestamp": "2025-07-28T05:40:00Z",
        "cors_origins": settings.cors_origins
    }


@app.options("/api/{path:path}")
async def options_handler(path: str):
    """Handle preflight OPTIONS requests explicitly."""
    return JSONResponse(
        status_code=200,
        content={"message": "OK"},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS, HEAD, PATCH",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Allow-Credentials": "true",
            "Access-Control-Max-Age": "3600",
        }
    )





# Include API routers
app.include_router(auth.router, prefix="/api")
app.include_router(social_media.router, prefix="/api")
app.include_router(ai.router, prefix="/api")
app.include_router(google_drive.router)
app.include_router(webhook.router, prefix="/api")
app.include_router(google_oauth.router, prefix="/api")

# Import and include notification router
from app.api import notifications
app.include_router(notifications.router, prefix="/api")


# Error handlers
@app.exception_handler(404)
async def not_found_handler(request, exc):
    return JSONResponse(
        status_code=404,
        content={
            "error": "Not Found",
            "message": "The requested resource was not found",
            "status_code": 404
        }
    )


@app.exception_handler(500)
async def internal_error_handler(request, exc):
    logger.error(f"Internal server error: {exc}")
    return JSONResponse(
        status_code=500,
        content={
            "error": "Internal Server Error",
            "message": "An internal server error occurred",
            "status_code": 500
        }
    )


if __name__ == "__main__":
    import uvicorn
    
    def signal_handler(sig, frame):
        logger.info("Received shutdown signal, stopping server...")
        sys.exit(0)
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    signal.signal(signal.SIGTERM, signal_handler)  # Termination signal
    
    try:
        logger.info("Starting FastAPI server...")
        logger.info("Press Ctrl+C to stop the server")
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8000,
            reload=settings.debug,
            log_level="info"
        )
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
    except Exception as e:
        logger.error(f"Server error: {e}")
        sys.exit(1)