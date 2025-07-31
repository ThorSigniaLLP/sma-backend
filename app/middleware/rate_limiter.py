"""
Rate limiting middleware to prevent overwhelming the server.
"""

import time
import asyncio
from collections import defaultdict, deque
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse
import logging

logger = logging.getLogger(__name__)

class RateLimiter:
    def __init__(self, max_requests_per_minute=600, max_concurrent_per_ip=50):
        self.max_requests_per_minute = max_requests_per_minute
        self.max_concurrent_per_ip = max_concurrent_per_ip
        
        # Track requests per IP per minute
        self.request_history = defaultdict(deque)
        
        # Track concurrent requests per IP
        self.concurrent_requests = defaultdict(int)
        
        # Lock for thread safety
        self.lock = asyncio.Lock()
    
    async def is_allowed(self, client_ip: str) -> tuple[bool, str]:
        """Check if the request is allowed based on rate limits."""
        async with self.lock:
            current_time = time.time()
            
            # Clean old requests (older than 1 minute)
            minute_ago = current_time - 60
            while (self.request_history[client_ip] and 
                   self.request_history[client_ip][0] < minute_ago):
                self.request_history[client_ip].popleft()
            
            # Check requests per minute limit
            if len(self.request_history[client_ip]) >= self.max_requests_per_minute:
                return False, f"Rate limit exceeded: {self.max_requests_per_minute} requests per minute"
            
            # Check concurrent requests limit
            if self.concurrent_requests[client_ip] >= self.max_concurrent_per_ip:
                return False, f"Too many concurrent requests: {self.max_concurrent_per_ip} max per IP"
            
            # Add current request
            self.request_history[client_ip].append(current_time)
            self.concurrent_requests[client_ip] += 1
            
            return True, ""
    
    async def release_request(self, client_ip: str):
        """Release a concurrent request slot."""
        async with self.lock:
            if self.concurrent_requests[client_ip] > 0:
                self.concurrent_requests[client_ip] -= 1

# Global rate limiter instance
rate_limiter = RateLimiter()

async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting middleware."""
    # Get client IP
    client_ip = request.client.host if request.client else "unknown"
    
    # Skip rate limiting for health checks, static files, and WebSocket connections
    skip_paths = ["/", "/health", "/docs", "/redoc"]
    skip_prefixes = ["/temp_images", "/ws/"]
    
    if (request.url.path in skip_paths or 
        any(request.url.path.startswith(prefix) for prefix in skip_prefixes)):
        return await call_next(request)
    
    # Check rate limit
    allowed, message = await rate_limiter.is_allowed(client_ip)
    
    if not allowed:
        logger.warning(f"Rate limit exceeded for {client_ip}: {message}")
        return JSONResponse(
            status_code=429,
            content={
                "error": "Too Many Requests",
                "message": message,
                "retry_after": 60,
                "status_code": 429
            },
            headers={"Retry-After": "60"}
        )
    
    try:
        # Process the request
        response = await call_next(request)
        return response
    finally:
        # Always release the concurrent request slot
        await rate_limiter.release_request(client_ip)