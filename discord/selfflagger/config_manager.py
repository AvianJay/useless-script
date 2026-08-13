import copy
import json
import os


CONFIG_VERSION = 6
DEFAULT_CONFIG = {
    "prefix": ">",
    "token": "",
    "owner_id": 0,
    "command_guild_id": 0,
    "scan_guilds": [
        {
            "id": 0,
            "flagged_roles": [0],
            "check_channels": [0],
        }
    ],
    "ignored_users": [],
}


class ConfigError(ValueError):
    pass


def _snowflake(value, field_name, *, allow_zero=True):
    if isinstance(value, bool):
        raise ConfigError(f"{field_name} must be a Discord ID")
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ConfigError(f"{field_name} must be a Discord ID") from error
    if normalized < 0 or (normalized == 0 and not allow_zero):
        raise ConfigError(f"{field_name} must be a Discord ID")
    return normalized


def _snowflake_list(value, field_name):
    if not isinstance(value, list):
        raise ConfigError(f"{field_name} must be a list")
    return [_snowflake(item, field_name) for item in value]


def normalize_config(raw_config):
    if not isinstance(raw_config, dict):
        raise ConfigError("config root must be a JSON object")

    normalized = copy.deepcopy(raw_config)
    for key, default_value in DEFAULT_CONFIG.items():
        normalized.setdefault(key, copy.deepcopy(default_value))

    prefix = normalized["prefix"]
    if not isinstance(prefix, str) or not prefix:
        raise ConfigError("prefix must be a non-empty string")
    if not isinstance(normalized["token"], str):
        raise ConfigError("token must be a string")

    normalized["owner_id"] = _snowflake(normalized["owner_id"], "owner_id")
    normalized["command_guild_id"] = _snowflake(
        normalized["command_guild_id"],
        "command_guild_id",
    )
    normalized["ignored_users"] = _snowflake_list(
        normalized["ignored_users"],
        "ignored_users",
    )

    scan_guilds = normalized["scan_guilds"]
    if not isinstance(scan_guilds, list):
        raise ConfigError("scan_guilds must be a list")
    normalized_scan_guilds = []
    for index, guild_info in enumerate(scan_guilds):
        if not isinstance(guild_info, dict):
            raise ConfigError(f"scan_guilds[{index}] must be an object")
        guild_info = copy.deepcopy(guild_info)
        guild_info["id"] = _snowflake(
            guild_info.get("id", 0),
            f"scan_guilds[{index}].id",
        )
        guild_info["flagged_roles"] = _snowflake_list(
            guild_info.get("flagged_roles", []),
            f"scan_guilds[{index}].flagged_roles",
        )
        guild_info.pop("viewable_channels", None)
        normalized_scan_guilds.append(guild_info)
    normalized["scan_guilds"] = normalized_scan_guilds
    normalized["config_version"] = CONFIG_VERSION
    return normalized


def load_config_file(path, *, allow_missing=True):
    if not os.path.exists(path):
        if not allow_missing:
            raise ConfigError("config file does not exist")
        return normalize_config({}), True
    try:
        with open(path, "r", encoding="utf-8") as config_file:
            raw_config = json.load(config_file)
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigError(f"could not read config: {type(error).__name__}") from error

    normalized = normalize_config(raw_config)
    return normalized, normalized != raw_config


def save_config_file(path, config):
    path = os.fspath(path)
    temporary_path = f"{path}.tmp"
    try:
        with open(temporary_path, "w", encoding="utf-8") as config_file:
            json.dump(config, config_file, ensure_ascii=False, indent=4)
            config_file.write("\n")
        os.replace(temporary_path, path)
    except OSError:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def command_access_allowed(
    *,
    author_id,
    self_user_id,
    owner_id,
    guild_id,
    command_guild_id,
):
    if guild_id is None:
        return False
    allowed_authors = {user_id for user_id in (self_user_id, owner_id) if user_id}
    if author_id not in allowed_authors:
        return False
    return not command_guild_id or guild_id == command_guild_id
