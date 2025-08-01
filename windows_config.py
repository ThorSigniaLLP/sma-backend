#!/usr/bin/env python3
"""
Windows-specific configuration and optimizations for the FastAPI server.
"""

import os
import sys
import gc
import threading
import time
from typing import Dict, Any

def configure_windows_limits():
    """Configure Windows-specific limits and optimizations."""
    try:
        # Set environment variables for better performance
        os.environ['PYTHONUNBUFFERED'] = '1'
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        
        # Configure garbage collection for better memory management
        gc.set_threshold(700, 10, 10)
        
        print("✅ Windows optimizations configured")
    except Exception as e:
        print(f"⚠️  Warning: Could not configure Windows optimizations: {e}")

def get_windows_uvicorn_config() -> Dict[str, Any]:
    """Get Windows-optimized Uvicorn configuration."""
    return {
        "host": "127.0.0.1",
        "port": 8000,
        "workers": 1,  # Single worker for Windows
        "loop": "asyncio",
        "access_log": True,
        "use_colors": True,
        "reload_dirs": ["app"],
        "reload_excludes": ["*.pyc", "__pycache__", "*.log"],
    }

def cleanup_resources():
    """Clean up resources on shutdown."""
    try:
        # Force garbage collection
        gc.collect()
        
        # Give threads time to cleanup
        time.sleep(0.1)
        
        print("✅ Resources cleaned up")
    except Exception as e:
        print(f"⚠️  Warning: Error during cleanup: {e}")

# Background resource monitor (optional)
class ResourceMonitor:
    """Simple resource monitor for development."""
    
    def __init__(self):
        self.running = False
        self.thread = None
    
    def start(self):
        """Start monitoring resources."""
        if not self.running:
            self.running = True
            self.thread = threading.Thread(target=self._monitor, daemon=True)
            self.thread.start()
    
    def stop(self):
        """Stop monitoring resources."""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
    
    def _monitor(self):
        """Monitor system resources."""
        while self.running:
            try:
                # Simple memory check
                gc.collect()
                time.sleep(30)  # Check every 30 seconds
            except Exception:
                break

# Global resource monitor instance
_resource_monitor = ResourceMonitor()

def start_resource_monitoring():
    """Start background resource monitoring."""
    _resource_monitor.start()

def stop_resource_monitoring():
    """Stop background resource monitoring."""
    _resource_monitor.stop()