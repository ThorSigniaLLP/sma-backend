#!/usr/bin/env python3
"""
FastAPI server startup script with NO concurrency limits to eliminate 503 errors.
"""

import uvicorn
import signal
import sys
import os
import ssl
from pathlib import Path
from app.config import get_settings

def main():
    """Start the FastAPI server with no concurrency limits."""
    
    settings = get_settings()
    
    def signal_handler(signum, frame):
        print("\n🛑 Received shutdown signal, stopping server...")
        sys.exit(0)
    
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)  # Termination signal
    
    # Check for SSL certificates
    cert_dir = Path(__file__).parent
    key_path = cert_dir / "localhost-key.pem"
    cert_path = cert_dir / "localhost.pem"
    
    # Set very high environment limits
    os.environ["UVICORN_LIMIT_CONCURRENCY"] = "999999"
    os.environ["UVICORN_LIMIT_MAX_REQUESTS"] = "999999"
    
    if not key_path.exists() or not cert_path.exists():
        print("❌ SSL certificates not found in backend directory!")
        print("🌐 Starting HTTP server with NO LIMITS...")
        
        print("🚀 Starting Automation Dashboard API (HTTP - NO LIMITS)...")
        print("📍 Server will be available at: http://localhost:8000")
        print("📚 API docs will be at: http://localhost:8000/docs")
        print("⚠️  Note: Facebook login requires HTTPS")
        print("🛑 Press Ctrl+C to stop the server")
        print("-" * 50)
        
        try:
            uvicorn.run(
                "app.main:app",
                host="0.0.0.0",
                port=8000,
                workers=1,
                limit_concurrency=None,  # NO LIMIT
                limit_max_requests=None,  # NO LIMIT
                timeout_keep_alive=120,
                timeout_graceful_shutdown=120,
                reload=settings.debug,
                log_level="warning",  # Reduce logging
                access_log=False,  # Disable access log
                use_colors=True,
                loop="asyncio"
            )
        except KeyboardInterrupt:
            print("\n👋 Server stopped by user")
        except Exception as e:
            print(f"❌ Server error: {e}")
            sys.exit(1)
    else:
        # HTTPS configuration with no limits
        print("🚀 Starting Automation Dashboard API (HTTPS - NO LIMITS)...")
        print("📍 Server will be available at: https://localhost:8000")
        print("📚 API docs will be at: https://localhost:8000/docs")
        print("🔒 Secure connection established")
        print("🎉 Facebook login should now work!")
        print("🛑 Press Ctrl+C to stop the server")
        print("-" * 50)
        
        try:
            uvicorn.run(
                "app.main:app",
                host="0.0.0.0",
                port=8000,
                workers=1,
                limit_concurrency=None,  # NO LIMIT
                limit_max_requests=None,  # NO LIMIT
                timeout_keep_alive=120,
                timeout_graceful_shutdown=120,
                ssl_keyfile=str(key_path),
                ssl_certfile=str(cert_path),
                reload=settings.debug,
                log_level="warning",  # Reduce logging
                access_log=False,  # Disable access log
                use_colors=True,
                loop="asyncio"
            )
        except KeyboardInterrupt:
            print("\n👋 Server stopped by user")
        except Exception as e:
            print(f"❌ Server error: {e}")
            sys.exit(1)

if __name__ == "__main__":
    main()