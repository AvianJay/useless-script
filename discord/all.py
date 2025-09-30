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

# Import all modules to register their events and commands
import ReportToBan
print("Imported ReportToBan")
import ModerationNotify
print("Imported ModerationNotify")
import dsize
print("Imported dsize")
import doomcord
print("Imported doomcord")
import PresenceChange
print("Imported PresenceChange")
import OwnerTools
print("Imported OwnerTools")

if __name__ == "__main__":
    start_bot()
