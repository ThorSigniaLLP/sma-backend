#!/usr/bin/env python3
"""
Minimal server configuration for testing Windows file descriptor fixes.
"""

import uvicorn
import asyncio
import sys
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config import get_settings
from windows_config import configure_windows_limits, get_windows_uvicorn_config

# Configure Windows optimizations
configure_windows_limits()

# Create minimal FastAPI app
app = FastAPI(title="Minimal Test Server")

# Add minimal CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def root():
    return {"message": "Minimal server running", "status": "ok"}

@app.get("/health")
async def health():
    return {"status": "healthy", "server": "minimal"}

if __name__ == "__main__":
    settings = get_settings()
    
    print("🚀 Starting minimal test server...")
    print("📍 Server will be available at: http://localhost:8000")
    
    try:
        # Get Windows-optimized configuration
        uvicorn_config = get_windows_uvicorn_config()
        uvicorn_config.update({
            "reload": False,  # Disable reload for testing
            "log_level": "info",
        })
        
        uvicorn.run("minimal_server:app", **uvicorn_config)
        
    except KeyboardInterrupt:
        print("\n👋 Minimal server stopped by user")
    except Exception as e:
        print(f"❌ Server error: {e}")
        sys.exit(1)