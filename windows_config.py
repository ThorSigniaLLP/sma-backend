"""
Windows-specific configuration and optimizations for the FastAPI server.
This module helps resolve file descriptor limits and other Windows-specific issues.
"""

import os
import sys
import asyncio
import logging

logger = logging.getLogger(__name__)

def configure_windows_limits():
    """Configure Windows-specific limits and optimizations."""
    
    # Set asyncio event loop policy for Windows
    if sys.platform == "win32":
        # Use ProactorEventLoop for better Windows compatibility
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
        logger.info("✅ Windows ProactorEventLoop policy set")
        
        # Set environment variables to limit connections - very high limits to prevent 503 errors
        os.environ.setdefault("UVICORN_LIMIT_CONCURRENCY", "10000")
        os.environ.setdefault("UVICORN_LIMIT_MAX_REQUESTS", "50000")
        
        # WebSocket-friendly timeouts
        os.environ.setdefault("ASYNCIO_TIMEOUT", "60")
        os.environ.setdefault("WEBSOCKET_TIMEOUT", "30")
        os.environ.setdefault("WEBSOCKET_PING_INTERVAL", "20")
        os.environ.setdefault("WEBSOCKET_PING_TIMEOUT", "10")
        
        logger.info("✅ Windows-specific limits configured")
        
        return True
    
    return False

def get_windows_uvicorn_config():
    """Get Windows-optimized uvicorn configuration."""
    return {
        "host": "localhost",
        "port": 8000,
        "workers": 1,  # Single worker for Windows
        "limit_concurrency": 10000,  # Very high limit to prevent 503 errors
        "limit_max_requests": 50000,  # Very high request limit
        "timeout_keep_alive": 60,  # Longer keep-alive for WebSockets
        "timeout_graceful_shutdown": 60,  # Longer graceful shutdown
        "ws_ping_interval": 20,  # WebSocket ping interval
        "ws_ping_timeout": 10,  # WebSocket ping timeout
        "use_colors": True,
        "loop": "asyncio",
        "access_log": False,  # Disable access log to reduce overhead
    }

def cleanup_resources():
    """Clean up resources to prevent file descriptor leaks."""
    try:
        # Force garbage collection
        import gc
        gc.collect()
        
        # Close any lingering asyncio resources
        if sys.platform == "win32":
            loop = asyncio.get_event_loop()
            if hasattr(loop, '_selector') and loop._selector:
                # Close selector to free file descriptors
                try:
                    loop._selector.close()
                except:
                    pass
        
        logger.info("✅ Resources cleaned up")
        return True
    except Exception as e:
        logger.error(f"❌ Error cleaning up resources: {e}")
        return False