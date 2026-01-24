"""
Steam OpenID Authentication for SimLaps Client.

Implements Steam authentication flow using a local callback server.
"""

import re
import asyncio
import webbrowser
from urllib.parse import urlencode, parse_qs, urlparse
from typing import Optional, Callable, Awaitable
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
import httpx


# Steam OpenID endpoints
STEAM_OPENID_URL = "https://steamcommunity.com/openid/login"
STEAM_OPENID_VERIFY_URL = "https://steamcommunity.com/openid/login"

# Local callback server config
CALLBACK_PORT = 27015
CALLBACK_HOST = "localhost"


class SteamAuthResult:
    """Result of Steam authentication attempt."""
    
    def __init__(
        self,
        success: bool,
        steam_id: Optional[str] = None,
        error: Optional[str] = None,
    ):
        self.success = success
        self.steam_id = steam_id
        self.error = error


class CallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler for Steam OpenID callback."""
    
    steam_id: Optional[str] = None
    error: Optional[str] = None
    received: bool = False
    
    def log_message(self, format, *args):
        """Suppress HTTP server logging."""
        pass
    
    def do_GET(self):
        """Handle GET request from Steam callback."""
        CallbackHandler.received = True
        
        # Parse query parameters
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        
        # Extract Steam ID from claimed_id
        claimed_id = params.get("openid.claimed_id", [None])[0]
        
        if claimed_id:
            # Extract Steam ID64 from URL
            # Format: https://steamcommunity.com/openid/id/76561198321627695
            match = re.search(r"/id/(\d+)$", claimed_id)
            if match:
                CallbackHandler.steam_id = match.group(1)
            else:
                CallbackHandler.error = "Could not parse Steam ID from response"
        else:
            CallbackHandler.error = "No Steam ID in callback"
        
        # Send response HTML
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        
        if CallbackHandler.steam_id:
            html = """
            <!DOCTYPE html>
            <html>
            <head>
                <title>SimLaps - Login Successful</title>
                <style>
                    body {
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                        background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
                        color: white;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                    }
                    .container {
                        text-align: center;
                        padding: 40px;
                        background: rgba(255, 255, 255, 0.1);
                        border-radius: 16px;
                        backdrop-filter: blur(10px);
                    }
                    .success-icon {
                        font-size: 64px;
                        margin-bottom: 20px;
                    }
                    h1 { margin: 0 0 10px 0; }
                    p { opacity: 0.8; }
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="success-icon">✓</div>
                    <h1>Login Successful!</h1>
                    <p>You can close this window and return to SimLaps Client.</p>
                </div>
            </body>
            </html>
            """
        else:
            html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>SimLaps - Login Failed</title>
                <style>
                    body {{
                        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                        background: linear-gradient(135deg, #2e1a1a 0%, #3e1621 100%);
                        color: white;
                        display: flex;
                        justify-content: center;
                        align-items: center;
                        height: 100vh;
                        margin: 0;
                    }}
                    .container {{
                        text-align: center;
                        padding: 40px;
                        background: rgba(255, 255, 255, 0.1);
                        border-radius: 16px;
                        backdrop-filter: blur(10px);
                    }}
                    .error-icon {{
                        font-size: 64px;
                        margin-bottom: 20px;
                    }}
                    h1 {{ margin: 0 0 10px 0; color: #ff6b6b; }}
                    p {{ opacity: 0.8; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="error-icon">✗</div>
                    <h1>Login Failed</h1>
                    <p>{CallbackHandler.error or 'Unknown error'}</p>
                    <p>Please close this window and try again.</p>
                </div>
            </body>
            </html>
            """
        
        self.wfile.write(html.encode())


class SteamAuth:
    """
    Handles Steam OpenID authentication flow.
    
    Opens browser for Steam login and listens for callback on local server.
    """
    
    def __init__(
        self,
        callback_port: int = CALLBACK_PORT,
        on_status: Optional[Callable[[str], Awaitable[None]]] = None,
    ):
        """
        Initialize Steam authentication handler.
        
        Args:
            callback_port: Port for local callback server
            on_status: Optional callback for status updates
        """
        self.callback_port = callback_port
        self.on_status = on_status
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[Thread] = None

    async def _emit_status(self, status: str) -> None:
        """Emit status update."""
        if self.on_status:
            await self.on_status(status)

    def _get_return_url(self) -> str:
        """Get the local callback URL."""
        return f"http://{CALLBACK_HOST}:{self.callback_port}/callback"

    def _get_auth_url(self) -> str:
        """Build Steam OpenID authentication URL."""
        return_url = self._get_return_url()
        
        params = {
            "openid.ns": "http://specs.openid.net/auth/2.0",
            "openid.mode": "checkid_setup",
            "openid.return_to": return_url,
            "openid.realm": f"http://{CALLBACK_HOST}:{self.callback_port}",
            "openid.identity": "http://specs.openid.net/auth/2.0/identifier_select",
            "openid.claimed_id": "http://specs.openid.net/auth/2.0/identifier_select",
        }
        
        return f"{STEAM_OPENID_URL}?{urlencode(params)}"

    def _start_callback_server(self) -> None:
        """Start the local callback server."""
        # Reset handler state
        CallbackHandler.steam_id = None
        CallbackHandler.error = None
        CallbackHandler.received = False
        
        self._server = HTTPServer(
            (CALLBACK_HOST, self.callback_port),
            CallbackHandler,
        )
        self._server.timeout = 1.0
        
        def serve():
            while not CallbackHandler.received:
                self._server.handle_request()
        
        self._server_thread = Thread(target=serve, daemon=True)
        self._server_thread.start()

    def _stop_callback_server(self) -> None:
        """Stop the local callback server."""
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._server_thread:
            self._server_thread.join(timeout=2.0)
            self._server_thread = None

    async def authenticate(self, timeout: float = 120.0) -> SteamAuthResult:
        """
        Perform Steam authentication.
        
        Opens browser for Steam login and waits for callback.
        
        Args:
            timeout: Maximum seconds to wait for authentication
            
        Returns:
            SteamAuthResult with success status and Steam ID
        """
        await self._emit_status("Starting authentication...")
        
        try:
            # Start callback server
            self._start_callback_server()
            await self._emit_status("Callback server started")
            
            # Open browser for Steam login
            auth_url = self._get_auth_url()
            await self._emit_status("Opening Steam login...")
            webbrowser.open(auth_url)
            
            # Wait for callback
            await self._emit_status("Waiting for Steam login...")
            start_time = asyncio.get_event_loop().time()
            
            while not CallbackHandler.received:
                if asyncio.get_event_loop().time() - start_time > timeout:
                    return SteamAuthResult(
                        success=False,
                        error="Authentication timed out",
                    )
                await asyncio.sleep(0.5)
            
            # Check result
            if CallbackHandler.steam_id:
                await self._emit_status(f"Authenticated as {CallbackHandler.steam_id}")
                return SteamAuthResult(
                    success=True,
                    steam_id=CallbackHandler.steam_id,
                )
            else:
                return SteamAuthResult(
                    success=False,
                    error=CallbackHandler.error or "Authentication failed",
                )
                
        except Exception as e:
            return SteamAuthResult(
                success=False,
                error=f"Authentication error: {str(e)}",
            )
        finally:
            self._stop_callback_server()

    async def verify_steam_id(self, steam_id: str) -> tuple[bool, Optional[dict]]:
        """
        Verify a Steam ID and get user info.
        
        Args:
            steam_id: Steam ID64 to verify
            
        Returns:
            Tuple of (valid, user_info)
        """
        # Note: This would require a Steam API key for full user info
        # For now, just validate the format
        if not steam_id or not steam_id.isdigit() or len(steam_id) != 17:
            return False, None
        
        if not steam_id.startswith("7656"):
            return False, None
        
        return True, {"steam_id": steam_id}


async def get_api_key_from_server(
    server_url: str,
    steam_id: str,
) -> tuple[Optional[str], Optional[str]]:
    """
    Exchange Steam ID for an API key from the server.
    
    This requires server-side support for client authentication.
    
    Args:
        server_url: SimLaps server URL
        steam_id: Authenticated Steam ID
        
    Returns:
        Tuple of (api_key, error_message)
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{server_url.rstrip('/')}/api/auth/client",
                json={"steamId": steam_id},
            )
            
            if response.status_code == 200:
                data = response.json()
                return data.get("apiKey"), None
            elif response.status_code == 401:
                return None, "Steam account not registered on SimLaps"
            elif response.status_code == 404:
                # Endpoint doesn't exist - user needs manual API key
                return None, "Server doesn't support client authentication. Please enter API key manually."
            else:
                return None, f"Server error: {response.status_code}"
                
    except httpx.NetworkError:
        return None, "Could not connect to server"
    except Exception as e:
        return None, f"Error: {str(e)}"
