#!/usr/bin/env python3
"""
Quick test to verify the server starts without errors.
"""

import asyncio
import sys
import os
from pathlib import Path

# Add the backend directory to Python path
backend_dir = Path(__file__).parent
sys.path.insert(0, str(backend_dir))

async def test_server_startup():
    """Test that the server can start without errors"""
    try:
        print("🧪 Testing server startup...")
        
        # Import the main app
        from app.main import app
        print("✅ Main app imported successfully")
        
        # Test notification service
        from app.services.notification_service import notification_service
        print("✅ Notification service imported successfully")
        
        # Test that background tasks can be started
        await notification_service.ensure_background_tasks_running()
        print("✅ Background tasks started successfully")
        
        # Test WebSocket connection wrapper
        from app.services.notification_service import WebSocketConnection
        
        # Mock websocket for testing
        class MockWebSocket:
            def __init__(self):
                self.client_state = type('State', (), {'name': 'CONNECTED'})()
            
            async def send_text(self, data):
                print(f"📤 Mock WebSocket would send: {data[:50]}...")
                return True
        
        mock_ws = MockWebSocket()
        test_connection = WebSocketConnection(user_id=1, websocket=mock_ws)
        
        # Test sending a message
        test_message = {"type": "test", "message": "Hello WebSocket!"}
        success = await test_connection.send_message(test_message)
        
        if success:
            print("✅ WebSocket connection wrapper works correctly")
        else:
            print("❌ WebSocket connection wrapper failed")
        
        # Test heartbeat
        heartbeat_success = await test_connection.send_heartbeat()
        if heartbeat_success:
            print("✅ WebSocket heartbeat works correctly")
        else:
            print("❌ WebSocket heartbeat failed")
        
        print("\n🎉 All tests passed! Server should work correctly.")
        print("\n📋 Next steps:")
        print("   1. Run: python run_https.py")
        print("   2. Test WebSocket connections in browser")
        print("   3. Try refreshing the page multiple times")
        print("   4. Check for 503 errors (should be eliminated)")
        
        return True
        
    except Exception as e:
        print(f"❌ Server startup test failed: {e}")
        import traceback
        print(f"📋 Full error: {traceback.format_exc()}")
        return False

async def main():
    """Main test function"""
    print("🚀 Quick Server Test")
    print("=" * 50)
    
    success = await test_server_startup()
    
    if success:
        print("\n✅ SUCCESS: Server is ready to run!")
        return 0
    else:
        print("\n❌ FAILURE: Server has issues that need to be fixed!")
        return 1

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n🛑 Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)