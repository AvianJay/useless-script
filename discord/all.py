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
from globalenv import bot, start_bot, config

modules = [
    "ReportToBan",
    "ModerationNotify",
    "dsize",
    "doomcord",
    "PresenceChange",
    "OwnerTools",
    "MultiModerate",
]

for mod in config("disabled_modules", []):
    if mod in modules:
        print(f"[!] Module {mod} is disabled in config.")
        modules.remove(mod)

# Import all modules to register their events and commands
for module in modules:
    __import__(module)
    print(f"[+] Module {module} loaded.")

if __name__ == "__main__":
    start_bot()
