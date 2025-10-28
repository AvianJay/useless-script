import asyncio
import g4f
import json
from datetime import datetime, timedelta
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
import os
import random
from database import db
from globalenv import bot, start_bot
import globalenv
import traceback

# Load modules from modules.json
try:
    with open('modules.json', 'r', encoding='utf-8') as f:
        modules = json.load(f)
except FileNotFoundError:
    print("[!] modules.json not found. Creating a default one.")
    default_modules = [
        "ReportSystem",
        "ModerationNotify",
        "dsize",
        "doomcord",
        "PresenceChange",
        "OwnerTools",
        "Moderate",
        "ItemSystem",
        "AutoModerate",
        "AutoPublish",
        "UtilCommands",
        "r34",
        "DynamicVoice",
        "twbus",
        "AutoReply",
        "logger"
    ]
    with open('modules.json', 'w', encoding='utf-8') as f:
        json.dump(default_modules, f, indent=4)
    modules = default_modules
except json.JSONDecodeError:
    print("[!] modules.json is not a valid JSON file. Please check its contents.")
    modules = []

globalenv.modules = modules
from logger import log
# print(f"[+] Loading {len(modules)} module(s)...")
log(f"Loading {len(modules)} module(s)...", module_name="all")

# Import all modules to register their events and commands
for module in modules:
    try:
        __import__(module)
        # print(f"[+] Module {module} loaded.")
        log(f"Module {module} loaded.", module_name="all")
    except Exception as e:
        # print(f"[!] Failed to load module {module}: {e}")
        log(f"Failed to load module {module}: {e}", module_name="all")
        traceback.print_exc()
        modules.remove(module)

if __name__ == "__main__":
    start_bot()
