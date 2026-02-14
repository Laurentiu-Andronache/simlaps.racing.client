"""
Discord Notifier Module

Handles posting lap times to Discord webhooks with proper formatting
and error handling.
"""

import httpx
import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, Any
from datetime import datetime


@dataclass
class LapData:
    """Data structure for lap information."""
    track_name: str
    car_name: str
    lap_time_ms: int
    valid: bool
    steam_id: str
    steam_name: Optional[str] = None
    is_personal_best: bool = False
    created_at: Optional[datetime] = None
    sector_times_ms: Optional[list[int]] = None  # [sector1, sector2, sector3] in ms
    fuel_used_liters: Optional[float] = None
    tire_compound: Optional[str] = None  # SC, SS, etc.


class DiscordNotifier:
    """
    Handles Discord webhook notifications for lap times.
    
    Provides methods to post lap data and test webhook connectivity.
    """
    
    def __init__(self, webhook_url: str, timeout: float = 10.0):
        """
        Initialize Discord notifier.
        
        Args:
            webhook_url: Discord webhook URL
            timeout: Request timeout in seconds
        """
        self.webhook_url = webhook_url
        self.timeout = timeout
    
    def format_lap_time(self, lap_time_ms: int) -> str:
        """
        Format lap time in minutes:seconds.milliseconds.
        
        Args:
            lap_time_ms: Lap time in milliseconds
            
        Returns:
            Formatted lap time string
        """
        total_seconds = lap_time_ms / 1000
        minutes = int(total_seconds // 60)
        seconds = total_seconds % 60
        return f"{minutes}:{seconds:06.3f}"
    
    def create_lap_embed(self, lap_data: LapData) -> Dict[str, Any]:
        """
        Create Discord embed for lap data.
        
        Args:
            lap_data: Lap information
            
        Returns:
            Discord embed dictionary
        """
        # Determine color based on lap validity and PB status
        if lap_data.is_personal_best:
            color = 5814783  # Gold for PB
        elif lap_data.valid:
            color = 3447003  # Blue for valid laps
        else:
            color = 15158332  # Red for invalid laps
        
        # Title with PB indicator (now included in field)
        title = "Lap Time Recorded"
        
        # Format sectors as code block
        sectors_code = ""
        if lap_data.sector_times_ms and len(lap_data.sector_times_ms) >= 3:
            s1, s2, s3 = lap_data.sector_times_ms[:3]
            sectors_code = f"```\nS1: {self.format_lap_time(s1)}\nS2: {self.format_lap_time(s2)}\nS3: {self.format_lap_time(s3)}\n```"
        
        # Format session details
        session_details = []
        if lap_data.fuel_used_liters is not None:
            session_details.append(f"⛽ Fuel: {lap_data.fuel_used_liters:.1f}L")
        if lap_data.tire_compound:
            session_details.append(f"🛞 Tires: {lap_data.tire_compound}")
        session_text = " • ".join(session_details) if session_details else ""
        
        # Build fields in requested order
        fields = []
        
        # Driver field (full width)
        field_name = "🫙 New PB" if lap_data.is_personal_best else "Lap Recorded"
        fields.append({
            "name": field_name,
            "value": f"**Driver:** {lap_data.steam_name or 'Unknown'}\n🏎️ **Car:** {lap_data.car_name.replace('_', ' ').replace('ks_', '').title()} • 🏁 **Track:** {lap_data.track_name.replace('_', ' ').title()} • ⏱️ **Lap Time:** {self.format_lap_time(lap_data.lap_time_ms)}",
            "inline": False
        })
        
        # Sector Times (code block)
        if sectors_code:
            fields.append({
                "name": "📊 Sector Times",
                "value": sectors_code,
                "inline": False
            })
        
        # Session Details
        if session_text:
            fields.append({
                "name": "📋 Session Details",
                "value": session_text,
                "inline": False
            })
        
        embed = {
            "title": title,
            "color": color,
            "fields": fields,
            "footer": {
                "text": "SimLaps Client"
            },
            "timestamp": lap_data.created_at.isoformat() if lap_data.created_at else None
        }
        
        return embed
    
    async def post_lap(self, lap_data: LapData) -> bool:
        """
        Post lap data to Discord webhook.
        
        Args:
            lap_data: Lap information to post
            
        Returns:
            True if successful, False otherwise
        """
        try:
            embed = self.create_lap_embed(lap_data)
            payload = {
                "embeds": [embed]
            }
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                # Discord returns 204 for successful webhook posts
                return response.status_code in (200, 204)
                
        except httpx.TimeoutException:
            print("Discord webhook request timed out")
            return False
        except httpx.RequestError as e:
            print(f"Discord webhook request failed: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error posting to Discord: {e}")
            return False
    
    async def send_test_message(self) -> bool:
        """
        Send a test message to verify webhook connectivity.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create a realistic test lap with sample data
            test_lap = LapData(
                track_name="laguna_seca",
                car_name="ks_porsche_992_gt3_cup",
                lap_time_ms=92295,
                valid=True,
                steam_id="76561198321627695",
                steam_name="TestUser",
                is_personal_best=True,
                created_at=datetime.now(),
                sector_times_ms=[28456, 32123, 31716],  # Sample sector times
                fuel_used_liters=3.2,
                tire_compound="SC",
            )
            
            # Use the same embed creation as real laps
            test_embed = self.create_lap_embed(test_lap)
            
            payload = {
                "embeds": [test_embed]
            }
            
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                return response.status_code in (200, 204)
                
        except Exception as e:
            print(f"Discord test message failed: {e}")
            return False
    
    @staticmethod
    def validate_webhook_url(url: str) -> bool:
        """
        Validate Discord webhook URL format.
        
        Args:
            url: Webhook URL to validate
            
        Returns:
            True if URL format is valid
        """
        if not url or not isinstance(url, str):
            return False
        
        url = url.strip()
        
        # Basic Discord webhook URL pattern
        return url.startswith("https://discord.com/api/webhooks/") and len(url) > 50


def create_discord_notifier(webhook_url: str) -> Optional[DiscordNotifier]:
    """
    Create Discord notifier instance with validation.
    
    Args:
        webhook_url: Discord webhook URL
        
    Returns:
        DiscordNotifier instance or None if invalid URL
    """
    if not DiscordNotifier.validate_webhook_url(webhook_url):
        return None
    
    return DiscordNotifier(webhook_url)
