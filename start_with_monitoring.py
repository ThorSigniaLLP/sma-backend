#!/usr/bin/env python3
"""
Start the FastAPI server with resource monitoring for debugging.
"""

import subprocess
import threading
import time
import sys
import os
from pathlib import Path

def run_server():
    """Run the main server."""
    try:
        subprocess.run([sys.executable, "run.py"], cwd=Path(__file__).parent)
    except KeyboardInterrupt:
        print("Server stopped")

def run_monitor():
    """Run resource monitoring."""
    time.sleep(10)  # Wait for server to start
    try:
        subprocess.run([sys.executable, "monitor_resources.py", "3600", "10"], 
                      cwd=Path(__file__).parent)
    except KeyboardInterrupt:
        print("Monitor stopped")

if __name__ == "__main__":
    print("🚀 Starting server with resource monitoring...")
    
    # Start server in a separate thread
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    
    # Start monitoring
    try:
        run_monitor()
    except KeyboardInterrupt:
        print("\n👋 Shutting down...")
        sys.exit(0)