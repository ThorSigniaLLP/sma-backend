
#!/usr/bin/env python3
"""
FastAPI server startup script with proper Ctrl+C handling for Windows.
"""

import uvicorn
import signal
import sys
import os
from pathlib import Path
from app.config import get_settings
from windows_config import configure_windows_limits, get_windows_uvicorn_config, cleanup_resources

def main():
    """Start the FastAPI server with proper signal handling."""
    
    settings = get_settings()
    
    # Configure Windows-specific optimizations
    configure_windows_limits()
    
    def signal_handler(signum, frame):
        print("\n🛑 Received shutdown signal, stopping server...")
        cleanup_resources()
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)  # Termination signal
    
    print("🚀 Starting Automation Dashboard API...")
    print("📍 Server will be available at: http://localhost:8000")
    print("📚 API docs will be at: http://localhost:8000/docs")
    print("🛑 Press Ctrl+C to stop the server")
    print("-" * 50)
    
    try:
        # Get Windows-optimized configuration
        uvicorn_config = get_windows_uvicorn_config()
        uvicorn_config.update({
            "reload": settings.debug,
            "log_level": "info" if settings.debug else "warning",
        })
        
        uvicorn.run("app.main:app", **uvicorn_config)
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user")
    except Exception as e:
        print(f"❌ Server error: {e}")
        sys.exit(1)
    finally:
        cleanup_resources()
        print("✅ Server shutdown complete")

if __name__ == "__main__":
    main() 