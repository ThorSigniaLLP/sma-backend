#!/usr/bin/env python3
"""
Resource monitoring script for Windows to track file descriptors and connections.
"""

import psutil
import time
import sys
import os
from datetime import datetime

def get_process_info():
    """Get current process resource information."""
    try:
        process = psutil.Process()
        return {
            "pid": process.pid,
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 2),
            "cpu_percent": process.cpu_percent(),
            "num_threads": process.num_threads(),
            "num_fds": process.num_handles() if sys.platform == "win32" else process.num_fds(),
            "connections": len(process.connections()),
            "status": process.status()
        }
    except Exception as e:
        return {"error": str(e)}

def monitor_resources(duration=60, interval=5):
    """Monitor resources for a specified duration."""
    print(f"🔍 Monitoring resources for {duration} seconds (interval: {interval}s)")
    print("=" * 80)
    
    start_time = time.time()
    max_fds = 0
    max_connections = 0
    
    while time.time() - start_time < duration:
        info = get_process_info()
        
        if "error" not in info:
            timestamp = datetime.now().strftime("%H:%M:%S")
            fds = info["num_fds"]
            connections = info["connections"]
            
            # Track maximums
            max_fds = max(max_fds, fds)
            max_connections = max(max_connections, connections)
            
            # Warning thresholds
            fd_warning = "⚠️" if fds > 100 else "✅"
            conn_warning = "⚠️" if connections > 50 else "✅"
            
            print(f"{timestamp} | FDs: {fds:3d} {fd_warning} | Conns: {connections:3d} {conn_warning} | "
                  f"Mem: {info['memory_mb']:6.1f}MB | CPU: {info['cpu_percent']:5.1f}% | "
                  f"Threads: {info['num_threads']:2d}")
            
            # Critical warning
            if fds > 200 or connections > 100:
                print(f"🚨 CRITICAL: High resource usage detected!")
        else:
            print(f"❌ Error getting process info: {info['error']}")
        
        time.sleep(interval)
    
    print("=" * 80)
    print(f"📊 Summary:")
    print(f"   Max File Descriptors: {max_fds}")
    print(f"   Max Connections: {max_connections}")
    
    if max_fds > 200:
        print("⚠️  High file descriptor usage detected. Consider:")
        print("   - Reducing database pool size")
        print("   - Increasing cleanup intervals")
        print("   - Limiting concurrent connections")

if __name__ == "__main__":
    try:
        duration = int(sys.argv[1]) if len(sys.argv) > 1 else 60
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 5
        monitor_resources(duration, interval)
    except KeyboardInterrupt:
        print("\n👋 Monitoring stopped by user")
    except Exception as e:
        print(f"❌ Error: {e}")