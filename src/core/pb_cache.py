"""
Personal Best Cache Service

Manages in-memory cache of personal best lap times for Discord integration.
Preloads from API and provides fast PB detection for new laps.
"""

import httpx
from dataclasses import dataclass
from typing import Any, Dict, Tuple, Optional
from datetime import datetime

from src.utils.structured_logger import (
    Component,
    log_debug,
    log_info,
    log_warning,
    log_error,
)


@dataclass
class PersonalBest:
    """Personal best entry for a track+car combination."""
    best_time_ms: int
    last_lap_id: Optional[str] = None
    updated_at: Optional[datetime] = None


class PBCache:
    """
    In-memory cache for personal best lap times.
    
    Key: (track_id, car_id) tuple
    Value: PersonalBest with fastest lap time
    """
    
    def __init__(self, server_url: str, timeout: float = 10.0):
        """
        Initialize PB cache.
        
        Args:
            server_url: Base URL for API server
            timeout: Request timeout in seconds
        """
        self.server_url = server_url.rstrip("/")
        self.timeout = timeout
        self._cache: Dict[Tuple[str, str], PersonalBest] = {}
        self._steam_id: Optional[str] = None
        self._loaded = False
    
    def _normalize_key(self, track_id: str, car_id: str) -> Tuple[str, str]:
        """
        Normalize track and car IDs for consistent key generation.
        
        Args:
            track_id: Track identifier
            car_id: Car identifier
            
        Returns:
            Normalized key tuple
        """
        # Convert to lowercase and strip whitespace for consistency
        return (track_id.lower().strip(), car_id.lower().strip())
    
    async def preload_from_api(self, steam_id: str) -> bool:
        """
        Preload personal bests from API endpoint.
        
        Args:
            steam_id: Steam ID64 of the user
            
        Returns:
            True if preload was successful, False otherwise
        """
        try:
            url = f"{self.server_url}/api/laptimes/pb/by-steam"
            params = {
                "steamId": steam_id,
                "includeAll": "false"  # Only valid laps for PB comparison
            }
            
            log_debug(Component.PB_CACHE, "Fetching PBs from API", url=url, params=params)

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                response = await client.get(url, params=params)

                log_debug(Component.PB_CACHE, "PB preload response", status_code=response.status_code)

                if response.status_code != 200:
                    log_warning(Component.PB_CACHE, "Failed to preload PBs", status_code=response.status_code)
                    return False

                data = response.json()
                # Validate into a replacement cache first.  A malformed row is a
                # malformed response, rather than a row that can safely be skipped:
                # this keeps a failed preload from replacing another user's state
                # with a partial result.
                replacement_cache = self._parse_preload_response(data)

                # There are no awaits between these assignments, so a successful
                # preload swaps all related state together from the event loop's
                # perspective.  Failed preloads never mutate any of it.
                self._cache = replacement_cache
                self._steam_id = steam_id
                self._loaded = True

                log_info(
                    Component.PB_CACHE,
                    "Preloaded personal bests",
                    count=len(replacement_cache),
                    steam_id=steam_id,
                )
                return True
                
        except httpx.TimeoutException:
            log_warning(Component.PB_CACHE, "PB preload request timed out")
            return False
        except httpx.RequestError as e:
            log_warning(Component.PB_CACHE, "PB preload request failed", error=str(e))
            return False
        except ValueError as e:
            log_warning(Component.PB_CACHE, "Invalid PB preload response", error=str(e))
            return False
        except Exception as e:
            log_error(Component.PB_CACHE, "Unexpected error during PB preload", error=str(e))
            return False

    def _parse_preload_response(self, data: Any) -> Dict[Tuple[str, str], PersonalBest]:
        """Validate and parse an API response without changing the live cache.

        The API payload is intentionally strict: every row must be a complete,
        well-typed PB entry.  This prevents silently accepting a partial response
        and makes malformed-row handling consistent with malformed top-level data.
        ``bestTime`` must be a positive ``int``; booleans and floating-point
        values (including NaN and infinity) are rejected.
        """
        if not isinstance(data, dict):
            raise ValueError("response must be an object")

        personal_bests = data.get("personalBests")
        if not isinstance(personal_bests, list):
            raise ValueError("personalBests must be a list")

        replacement_cache: Dict[Tuple[str, str], PersonalBest] = {}
        for index, pb in enumerate(personal_bests):
            if not isinstance(pb, dict):
                raise ValueError(f"personalBests[{index}] must be an object")

            track_id = pb.get("trackId")
            car_id = pb.get("carId")
            best_time = pb.get("bestTime")
            if not isinstance(track_id, str) or not track_id.strip():
                raise ValueError(
                    f"personalBests[{index}].trackId must be a non-empty string"
                )
            if not isinstance(car_id, str) or not car_id.strip():
                raise ValueError(
                    f"personalBests[{index}].carId must be a non-empty string"
                )
            # An int is finite by definition.  Checking the exact type also
            # rejects bool (a subclass of int), floats, NaN, and infinity.
            if type(best_time) is not int or best_time <= 0:
                raise ValueError(
                    f"personalBests[{index}].bestTime must be a positive integer"
                )

            set_at = pb.get("setAt")
            updated_at = None
            if set_at is not None:
                if not isinstance(set_at, str) or not set_at:
                    raise ValueError(
                        f"personalBests[{index}].setAt must be an ISO timestamp or null"
                    )
                try:
                    updated_at = datetime.fromisoformat(set_at.replace("Z", "+00:00"))
                except (TypeError, ValueError) as e:
                    raise ValueError(
                        f"personalBests[{index}].setAt is not a valid ISO timestamp"
                    ) from e

            key = self._normalize_key(track_id, car_id)
            replacement_cache[key] = PersonalBest(
                best_time_ms=best_time,
                updated_at=updated_at,
            )

        return replacement_cache
    
    def check_and_update_pb(self, track_id: str, car_id: str, lap_time_ms: int) -> bool:
        """
        Check if a lap time is a new personal best and update cache if so.
        
        Args:
            track_id: Track identifier
            car_id: Car identifier
            lap_time_ms: Lap time in milliseconds
            
        Returns:
            True if this is a new personal best, False otherwise
        """
        log_debug(Component.PB_CACHE, "Checking PB", track=track_id, car=car_id, time_ms=lap_time_ms)
        
        key = self._normalize_key(track_id, car_id)
        current = self._cache.get(key)
        
        # If no existing PB or new time is faster, update and return True
        if current is None or lap_time_ms < current.best_time_ms:
            new_pb = PersonalBest(best_time_ms=lap_time_ms, updated_at=datetime.now())
            self._cache[key] = new_pb
            log_info(Component.PB_CACHE, "New personal best!", track=track_id, car=car_id, time_ms=lap_time_ms)
            return True
        
        log_debug(Component.PB_CACHE, "Not a PB", current_ms=current.best_time_ms, new_ms=lap_time_ms)
        return False
    
    def get_personal_best(self, track_id: str, car_id: str) -> Optional[PersonalBest]:
        """
        Get current personal best for a track+car combination.
        
        Args:
            track_id: Track identifier
            car_id: Car identifier
            
        Returns:
            PersonalBest entry or None if not found
        """
        if not self._loaded:
            return None
        
        key = self._normalize_key(track_id, car_id)
        return self._cache.get(key)
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        return {
            "loaded": self._loaded,
            "steam_id": self._steam_id,
            "combo_count": len(self._cache),
            "oldest_entry": min(
                (pb.updated_at for pb in self._cache.values() if pb.updated_at),
                default=None
            ),
            "newest_entry": max(
                (pb.updated_at for pb in self._cache.values() if pb.updated_at),
                default=None
            )
        }
    
    def clear(self) -> None:
        """Clear the cache."""
        self._cache.clear()
        self._steam_id = None
        self._loaded = False
    
    def is_loaded(self) -> bool:
        """Check if cache has been loaded."""
        return self._loaded

    def get_steam_id(self) -> Optional[str]:
        """Get the Steam ID associated with this cache."""
        return self._steam_id
    
    def get_all_pbs(self) -> Dict[Tuple[str, str], PersonalBest]:
        """
        Get all personal bests from cache.
        
        Returns:
            Dictionary with (track, car) keys and PersonalBest values
        """
        return {key: value for key, value in self._cache.items()}
