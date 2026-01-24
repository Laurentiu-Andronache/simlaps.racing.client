"""
Security module for SimLaps Client.

Handles payload signing, game process verification, and anti-cheat measures.
"""

import hmac
import hashlib
import uuid
import time
import os
from typing import Optional

# Try to import psutil, with fallback
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


# =============================================================================
# OBFUSCATED APP SECRET
# =============================================================================
# This placeholder will be replaced at build time by build.py
# The secret is XOR-encoded for basic obfuscation (PyArmor adds more protection)
# Format: XOR-encoded bytes as hex string
_ENCODED_SECRET = "PLACEHOLDER_SECRET_WILL_BE_REPLACED_AT_BUILD_TIME"

# XOR key for basic runtime deobfuscation
_XOR_KEY = 0x5A


def _decode_secret(encoded: str) -> bytes:
    """Decode the XOR-encoded secret."""
    if encoded.startswith("PLACEHOLDER"):
        # Development mode - use a dev secret
        return b"dev-secret-do-not-use-in-production-12345678"
    
    try:
        # Decode hex string and XOR each byte
        raw_bytes = bytes.fromhex(encoded)
        return bytes([b ^ _XOR_KEY for b in raw_bytes])
    except (ValueError, TypeError):
        # Fallback for invalid encoding
        return b"invalid-secret"


def get_app_secret() -> bytes:
    """
    Get the application secret for signing.
    
    The secret is embedded at build time and obfuscated.
    """
    return _decode_secret(_ENCODED_SECRET)


# =============================================================================
# GAME PROCESS DETECTION
# =============================================================================

# Known ACE process names
GAME_PROCESS_NAMES = [
    "AssettoCorsaEVO.exe",      # Main game executable
    "AC2-Win64-Shipping.exe",   # Alternative (Unreal shipping build)
]


def is_game_running() -> bool:
    """
    Check if Assetto Corsa Evo is currently running.
    
    This prevents log file manipulation when the game isn't running.
    
    Returns:
        True if ACE process is detected, False otherwise
    """
    if not PSUTIL_AVAILABLE:
        # If psutil not available, assume game is running (less secure)
        return True
    
    try:
        for proc in psutil.process_iter(['name']):
            try:
                proc_name = proc.info.get('name', '')
                if proc_name and proc_name in GAME_PROCESS_NAMES:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                # Process disappeared or we can't access it
                continue
    except Exception:
        # On any error, fail open (assume game running)
        return True
    
    return False


def get_game_process_info() -> Optional[dict]:
    """
    Get information about the running ACE process.
    
    Returns:
        Dict with process info if found, None otherwise
    """
    if not PSUTIL_AVAILABLE:
        return None
    
    try:
        for proc in psutil.process_iter(['name', 'pid', 'create_time']):
            try:
                proc_name = proc.info.get('name', '')
                if proc_name and proc_name in GAME_PROCESS_NAMES:
                    return {
                        'name': proc_name,
                        'pid': proc.info.get('pid'),
                        'start_time': proc.info.get('create_time'),
                    }
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    
    return None


# =============================================================================
# PAYLOAD SIGNING
# =============================================================================

def generate_nonce() -> str:
    """Generate a unique nonce for replay prevention."""
    return str(uuid.uuid4())


def get_timestamp() -> int:
    """Get current timestamp in milliseconds."""
    return int(time.time() * 1000)


def create_signature(
    timestamp: int,
    nonce: str,
    user_id: str,
    track_id: str,
    lap_time: int,
) -> str:
    """
    Create HMAC-SHA256 signature for a lap submission.
    
    Args:
        timestamp: Unix timestamp in milliseconds
        nonce: Unique submission identifier
        user_id: Steam ID of the user
        track_id: Track identifier
        lap_time: Lap time in milliseconds
        
    Returns:
        Hex-encoded signature string
    """
    # Create the signature data string
    # Order matters - must match server verification
    sig_data = f"{timestamp}:{nonce}:{user_id}:{track_id}:{lap_time}"
    
    # Create HMAC-SHA256 signature
    signature = hmac.new(
        get_app_secret(),
        sig_data.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return signature


def sign_payload(payload: dict) -> dict:
    """
    Sign a lap submission payload.
    
    Adds timestamp, nonce, and signature to the payload for server verification.
    
    Args:
        payload: The lap data to sign (must contain userId, trackId, time)
        
    Returns:
        New dict with original payload plus security fields
    """
    timestamp = get_timestamp()
    nonce = generate_nonce()
    
    # Extract required fields for signature
    user_id = str(payload.get('userId', ''))
    track_id = str(payload.get('trackId', ''))
    lap_time = int(payload.get('time', 0))
    
    # Create signature
    signature = create_signature(
        timestamp=timestamp,
        nonce=nonce,
        user_id=user_id,
        track_id=track_id,
        lap_time=lap_time,
    )
    
    # Return payload with security fields
    return {
        **payload,
        '_timestamp': timestamp,
        '_nonce': nonce,
        '_signature': signature,
    }


def verify_signature_locally(signed_payload: dict) -> bool:
    """
    Verify a signed payload locally (for testing).
    
    Args:
        signed_payload: Payload with _timestamp, _nonce, _signature
        
    Returns:
        True if signature is valid
    """
    try:
        timestamp = signed_payload.get('_timestamp', 0)
        nonce = signed_payload.get('_nonce', '')
        signature = signed_payload.get('_signature', '')
        
        user_id = str(signed_payload.get('userId', ''))
        track_id = str(signed_payload.get('trackId', ''))
        lap_time = int(signed_payload.get('time', 0))
        
        expected = create_signature(
            timestamp=timestamp,
            nonce=nonce,
            user_id=user_id,
            track_id=track_id,
            lap_time=lap_time,
        )
        
        # Use constant-time comparison
        return hmac.compare_digest(signature, expected)
    except Exception:
        return False


# =============================================================================
# ANTI-CHEAT UTILITIES
# =============================================================================

def get_security_status() -> dict:
    """
    Get current security status for display in UI.
    
    Returns:
        Dict with security-related status information
    """
    game_running = is_game_running()
    game_info = get_game_process_info() if game_running else None
    
    return {
        'game_running': game_running,
        'game_process': game_info,
        'psutil_available': PSUTIL_AVAILABLE,
        'secret_configured': not _ENCODED_SECRET.startswith("PLACEHOLDER"),
    }
