"""
SimLaps Client Version Information

Single source of truth for version numbers.
"""

# Game name
GAME_NAME = "SimLaps Client"

# Version components
VERSION_MAJOR = 1
VERSION_MINOR = 0
VERSION_PATCH = 1

# Full version string
VERSION = f"{VERSION_MAJOR}.{VERSION_MINOR}.{VERSION_PATCH}"

# Build metadata (set during build process)
BUILD_DATE = None
BUILD_COMMIT = None

# Minimum compatible server API version
MIN_SERVER_VERSION = "1.0.0"

# User-Agent string for API requests
USER_AGENT = f"SimLaps-Client/{VERSION}"


def get_version() -> str:
    """Get the version string."""
    return VERSION


def get_version_tuple() -> tuple[int, int, int]:
    """Get version as tuple for comparison."""
    return (VERSION_MAJOR, VERSION_MINOR, VERSION_PATCH)


def is_compatible_with_server(server_version: str) -> bool:
    """
    Check if this client is compatible with the given server version.
    
    Args:
        server_version: Server version string (e.g., "0.1.0")
        
    Returns:
        True if compatible
    """
    try:
        parts = server_version.split(".")
        server_tuple = tuple(int(p) for p in parts[:3])
        min_tuple = tuple(int(p) for p in MIN_SERVER_VERSION.split("."))
        return server_tuple >= min_tuple
    except (ValueError, IndexError):
        return False
