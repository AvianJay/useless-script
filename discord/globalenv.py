import os
import json
import discord
from discord.ext import commands
from discord import app_commands

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)

# Global configuration for backward compatibility (mainly for TOKEN)
config_version = 3
config_path = 'config.json'

default_config = {
    "config_version": config_version,
    "TOKEN": "YOUR_BOT_TOKEN_HERE",  # 機器人 Token
}
_config = None

try:
    if os.path.exists(config_path):
        _config = json.load(open(config_path, "r"))
        if not isinstance(_config, dict):
            print("[!] Config file is not a valid JSON object, resetting to default config.")
            _config = default_config.copy()
        for key in _config.keys():
            if key in default_config and not isinstance(_config[key], type(default_config[key])):
                print(f"[!] Config key '{key}' has an invalid type, resetting to default value.")
                _config[key] = default_config[key]
        if "config_version" not in _config:
            print("[!] Config file does not have 'config_version', resetting to default config.")
            _config = default_config.copy()
    else:
        _config = default_config.copy()
        json.dump(_config, open(config_path, "w"), indent=4)
except ValueError:
    _config = default_config.copy()
    json.dump(_config, open(config_path, "w"), indent=4)

if _config.get("config_version", 0) < config_version:
    print("[+] Updating config file from version",
          _config.get("config_version", 0),
          "to version",
          config_version
          )
    for k in default_config.keys():
        if _config.get(k) is None:
            _config[k] = default_config[k]
    _config["config_version"] = config_version
    print("[+] Saving...")
    json.dump(_config, open(config_path, "w"), indent=4)
    print("[+] Done.")

def config(key, value=None, mode="r"):
    if mode == "r":
        return _config.get(key)
    elif mode == "w":
        _config[key] = value
        json.dump(_config, open(config_path, "w"), indent=4)
        return True
    else:
        raise ValueError(f"Invalid mode: {mode}")


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    try:
        synced = await bot.tree.sync()  # 同步
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error syncing commands: {e}")


def start_bot():
    bot.run(config("TOKEN"))