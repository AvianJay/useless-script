import base64
import secrets
from datetime import datetime, timezone


def build_api_key(discord_id: str, updated_ts: int, secret: str) -> str:
    discord_id_b64 = base64.urlsafe_b64encode(discord_id.encode()).decode()
    updated_ts_b64 = base64.urlsafe_b64encode(str(updated_ts).encode()).decode()
    return f"uo-{discord_id_b64}-{updated_ts_b64}-{secret}"


def generate_api_key(discord_id: str) -> str:
    updated_ts = int(datetime.now(timezone.utc).timestamp())
    secret = secrets.token_urlsafe(32)
    return build_api_key(discord_id, updated_ts, secret)


def validate_api_key_structure(api_key: str) -> str | None:
    if not api_key:
        return None

    try:
        prefix, discord_id_b64, timestamp_b64, _secret = api_key.split("-", 3)
        if prefix != "uo":
            return None
        discord_id = base64.urlsafe_b64decode(discord_id_b64.encode()).decode()
        int(base64.urlsafe_b64decode(timestamp_b64.encode()).decode())
        return discord_id
    except Exception:
        return None
