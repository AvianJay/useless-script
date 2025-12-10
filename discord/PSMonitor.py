import discord
from discord.ext import commands, tasks
import psutil
from globalenv import bot, config
import asyncio

class PSMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cpu_history = []
        self.memory_history = []
        self.monitor_processes.start()

    def cog_unload(self):
        self.monitor_processes.cancel()

    @tasks.loop(minutes=5)
    async def monitor_processes(self):
        channel_id = config('process_monitor_channel_id')
        alert_channel_id = config('process_monitor_alert_channel_id')
        if not channel_id:
            return

        channel = self.bot.get_channel(channel_id)
        alert_channel = self.bot.get_channel(alert_channel_id)
        if not channel or not alert_channel:
            return

        alerts = []
        cpu_highest_process = None
        memory_highest_process = None
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
            try:
                cpu_threshold = config('cpu_usage_threshold', 80)
                mem_threshold = config('memory_usage_threshold', 80)

                if proc.info['cpu_percent'] > cpu_threshold:
                    alerts.append(f"偵測到高 CPU 佔用程序: {proc.info['name']} (PID: {proc.info['pid']}) - {proc.info['cpu_percent']}%")

                if proc.info['memory_percent'] > mem_threshold:
                    alerts.append(f"偵測到高記憶體佔用程序: {proc.info['name']} (PID: {proc.info['pid']}) - {proc.info['memory_percent']}%")
                
                if (not cpu_highest_process) or (proc.info['cpu_percent'] > cpu_highest_process.info['cpu_percent']):
                    cpu_highest_process = proc
                if (not memory_highest_process) or (proc.info['memory_percent'] > memory_highest_process.info['memory_percent']):
                    memory_highest_process = proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        if alerts:
            alert_message = "\n".join(alerts)
            await alert_channel.send(f"**程序資源佔用警告:**\n{alert_message}")
        
        cpu_usage = psutil.cpu_percent(interval=1)
        memory_info = psutil.virtual_memory().percent
        self.cpu_history.append(cpu_usage)
        self.memory_history.append(memory_info)
        self.cpu_history = self.cpu_history[-12:]
        self.memory_history = self.memory_history[-12:]
        # render in embed
        embed = discord.Embed(title="系統資源佔用", color=0x00ff00)
        embed.add_field(name="CPU 佔用 (%)", value=", ".join(map(str, self.cpu_history)), inline=False)
        embed.add_field(name="記憶體佔用 (%)", value=", ".join(map(str, self.memory_history)), inline=False)
        if cpu_highest_process:
            embed.add_field(name="最高 CPU 佔用程序", value=f"{cpu_highest_process.info['name']} (PID: {cpu_highest_process.info['pid']}) - {cpu_highest_process.info['cpu_percent']}%", inline=False)
        if memory_highest_process:
            embed.add_field(name="最高記憶體佔用程序", value=f"{memory_highest_process.info['name']} (PID: {memory_highest_process.info['pid']}) - {memory_highest_process.info['memory_percent']}%", inline=False)
        await channel.send(embed=embed)

    @monitor_processes.before_loop
    async def before_monitor_processes(self):
        await self.bot.wait_until_ready()

asyncio.run(bot.add_cog(PSMonitor(bot)))