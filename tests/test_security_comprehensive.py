"""
Comprehensive tests for security module.

Tests signing, nonce generation, and timestamp functions.
"""

import pytest
from src.core.security import (
    get_app_secret,
    generate_nonce,
    get_timestamp,
    create_signature,
    sign_payload,
    verify_signature_locally,
    is_game_running,
    get_game_process_info,
    get_steam_user,
)


@pytest.mark.usefixtures("configured_app_secret")
class TestSecretManagement:
    """Test secret management."""

    def test_get_app_secret(self):
        """Test getting secret from environment."""
        result = get_app_secret()
        assert isinstance(result, bytes)
        assert len(result) > 0

    def test_get_app_secret_requires_configured_secret(self, monkeypatch):
        """The signing guard must report a missing secret explicitly."""
        monkeypatch.setattr("src.core.security.APP_SECRET", None)

        with pytest.raises(RuntimeError, match="APP_SECRET environment variable not set"):
            get_app_secret()


@pytest.mark.usefixtures("configured_app_secret")
class TestSigning:
    """Test payload signing and verification."""

    def test_create_signature(self):
        """Test creating a signature."""
        signature = create_signature(
            timestamp=1234567890,
            nonce="test-nonce",
            user_id="76561198321627695",
            track_id="spa_francorchamps",
            lap_time=83456
        )

        assert signature is not None
        assert isinstance(signature, str)
        assert len(signature) > 0

    def test_sign_payload_valid(self):
        """Test signing a valid payload."""
        payload = {
            "userId": "76561198321627695",
            "trackId": "spa_francorchamps",
            "time": 83456
        }

        result = sign_payload(payload)

        assert result is not None
        assert "_signature" in result
        assert "_timestamp" in result
        assert "_nonce" in result

    def test_sign_payload_with_all_fields(self):
        """Test signing payload with all expected fields."""
        payload = {
            "userId": "76561198321627695",
            "trackId": "spa_francorchamps",
            "carId": "porsche_992_gt3_cup",
            "time": 83456,
            "sector1": 45000,
            "sector2": 48000,
            "sector3": -1,
            "gameVersion": "1.0.0",
            "tires": "S"
        }

        result = sign_payload(payload)

        assert result is not None
        assert "_signature" in result
        assert result["userId"] == "76561198321627695"

    def test_verify_signature_locally(self):
        """Test local signature verification."""
        payload = {
            "userId": "76561198321627695",
            "trackId": "spa_francorchamps",
            "time": 83456
        }
        signed = sign_payload(payload)

        result = verify_signature_locally(signed)

        assert result is True

    def test_verify_signature_locally_invalid(self):
        """Test verification with invalid signature."""
        payload = {
            "userId": "76561198321627695",
            "trackId": "spa_francorchamps",
            "time": 83456
        }
        signed = sign_payload(payload)
        signed["_signature"] = "invalid_signature"

        result = verify_signature_locally(signed)

        assert result is False


class TestNonceAndTimestamp:
    """Test nonce and timestamp generation."""

    def test_generate_nonce_unique(self):
        """Test that nonces are unique."""
        nonce1 = generate_nonce()
        nonce2 = generate_nonce()
        
        assert nonce1 != nonce2
        assert isinstance(nonce1, str)
        assert len(nonce1) > 0

    def test_generate_nonce_format(self):
        """Test nonce format."""
        nonce = generate_nonce()
        
        # Should be a UUID string
        assert len(nonce) == 36  # UUID format: xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

    def test_get_timestamp(self):
        """Test timestamp generation."""
        timestamp = get_timestamp()
        
        # Timestamp should be an integer
        assert isinstance(timestamp, int)
        # Should be a reasonable timestamp (milliseconds since epoch)
        assert timestamp > 0
        assert timestamp < 10**15

    def test_get_timestamp_format(self):
        """Test timestamp format."""
        timestamp = get_timestamp()
        
        # Should be a reasonable millisecond timestamp
        assert timestamp > 0
        assert timestamp < 10**15  # Not too far in the future (milliseconds)


@pytest.mark.usefixtures("configured_app_secret")
class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_sign_payload_minimal(self):
        """Test signing minimal payload."""
        payload = {"userId": "76561198321627695", "trackId": "test", "time": 1000}

        result = sign_payload(payload)

        assert result is not None
        assert "_signature" in result


@pytest.mark.usefixtures("configured_app_secret")
class TestIntegration:
    """Test integration scenarios."""

    def test_full_signing_flow(self):
        """Test complete signing flow."""
        payload = {
            "userId": "76561198321627695",
            "trackId": "spa_francorchamps",
            "carId": "porsche_992_gt3_cup",
            "time": 83456,
            "sector1": 45000,
            "sector2": 48000,
            "sector3": -1,
            "gameVersion": "1.0.0",
            "tires": "S"
        }

        signed = sign_payload(payload)
        verified = verify_signature_locally(signed)

        assert verified is True
        assert signed["userId"] == "76561198321627695"


class TestGameDetection:
    """Test game process detection."""

    def test_is_game_running_integration(self):
        """Game detection always reports a status, including off-platform."""
        result = is_game_running()

        from src.core.security import GameProcessStatus
        assert isinstance(result, GameProcessStatus)
        assert result in [GameProcessStatus.RUNNING, GameProcessStatus.NOT_RUNNING, GameProcessStatus.UNKNOWN]

    def test_get_game_process_info_integration(self):
        """Process details are optional, but never raise for an unavailable game."""
        result = get_game_process_info()

        assert result is None or isinstance(result, dict)


class TestSteamUser:
    """Test Steam user detection."""

    def test_get_steam_user_integration(self):
        """Steam detection returns an empty identity when unavailable."""
        steam_id, username = get_steam_user()

        assert (steam_id is None and username is None) or (steam_id is not None)


@pytest.mark.usefixtures("configured_app_secret")
class TestSigningEdgeCases:
    """Test signing edge cases."""

    def test_sign_payload_missing_required_fields(self):
        """Test sign_payload with missing required fields."""
        payload = {"userId": "76561198321627695"}  # Missing trackId and time

        result = sign_payload(payload)

        # sign_payload preserves the original mapping, uses empty/zero values
        # for absent fields while building the HMAC input, and adds security
        # fields without inventing missing business fields.
        assert "trackId" not in result
        assert "time" not in result
        assert "_signature" in result
        assert verify_signature_locally(result) is True

    def test_verify_signature_missing_fields(self):
        """Test verify_signature_locally with missing signature fields."""
        payload = {"userId": "76561198321627695"}  # Missing _signature

        result = verify_signature_locally(payload)

        assert result is False

    def test_verify_signature_none_payload(self):
        """Test verify_signature_locally with None payload."""
        result = verify_signature_locally(None)
        
        assert result is False
