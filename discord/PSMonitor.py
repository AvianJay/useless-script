import discord
from discord.ext import commands, tasks
import psutil
from globalenv import bot, config, start_bot
import asyncio

class PSMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.cpu_history = []
        self.memory_history = []
        
    async def cog_unload(self):
        self.monitor_processes.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.monitor_processes.is_running():
            self.monitor_processes.start()

    @tasks.loop(minutes=5)
    async def monitor_processes(self):
        channel_id = config('process_monitor_channel_id')
        alert_channel_id = config('process_monitor_alert_channel_id')

        channel = self.bot.get_channel(channel_id)
        alert_channel = self.bot.get_channel(alert_channel_id)
        if not channel or not alert_channel:
            return

        alerts = []
        cpu_highest_process = None
        memory_highest_process = None
        
        # Run process iteration in executor to prevent blocking
        def get_process_data():
            _alerts = []
            _cpu_highest = None
            _mem_highest = None
            cpu_threshold = config('cpu_usage_threshold', 80)
            mem_threshold = config('memory_usage_threshold', 80)
            
            for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_percent']):
                try:
                    p_info = proc.info
                    p_cpu = p_info['cpu_percent']
                    p_mem = p_info['memory_percent']
                    p_mem_mb = proc.memory_info().rss / 1024 / 1024

                    # psutil.process_iter cpu_percent might be 0 on first call or instant
                    # But for now we stick to what was there, maybe users are fine with it or it works due to internal state.
                    # Note: process.cpu_percent() returns usage since LAST call. 
                    # If this runs every 5 mins, it returns average usage over 5 mins.
                    
                    if p_cpu is not None and p_cpu > cpu_threshold:
                        _alerts.append(f"偵測到高 CPU 佔用程序: {p_info['name']} (PID: {p_info['pid']}) - {p_cpu}%")

                    if p_mem is not None and p_mem > mem_threshold:
                        _alerts.append(f"偵測到高記憶體佔用程序: {p_info['name']} (PID: {p_info['pid']}) - {p_mem_mb:.2f}MB ({p_mem}%)")
                    
                    if (not _cpu_highest) or (p_cpu is not None and (_cpu_highest['cpu_percent'] is None or p_cpu > _cpu_highest['cpu_percent'])):
                        _cpu_highest = p_info
                    if (not _mem_highest) or (p_mem is not None and (_mem_highest['memory_percent'] is None or p_mem > _mem_highest['memory_percent'])):
                        _mem_highest = p_info
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            return _alerts, _cpu_highest, _mem_highest

        alerts, cpu_highest_process_info, memory_highest_process_info = await self.bot.loop.run_in_executor(None, get_process_data)

        if alerts:
            alert_message = "\n".join(alerts)
            if len(alert_message) > 1900: # Truncate if too long (Discord limit)
                alert_message = alert_message[:1900] + "\n...(截斷)"
            await alert_channel.send(f"**程序資源佔用警告:**\n{alert_message}")
        
        # Run blocking cpu_percent in executor
        cpu_usage = await self.bot.loop.run_in_executor(None, psutil.cpu_percent, 1)
        # virtual_memory is fast (reading /proc/meminfo), usually fine to run in main thread, but being safe
        memory_info = psutil.virtual_memory().percent
        
        # memory usage in mb
        memory_usage = psutil.virtual_memory().used / 1024 / 1024
        memory_total = psutil.virtual_memory().total / 1024 / 1024
        
        self.cpu_history.append(cpu_usage)
        self.memory_history.append(memory_info)
        self.cpu_history = self.cpu_history[-12:]
        self.memory_history = self.memory_history[-12:]
        
        # render in embed
        embed = discord.Embed(title="系統資源佔用", color=0x00ff00)
        embed.add_field(name="CPU 佔用 (%)", value=", ".join(map(str, self.cpu_history)), inline=False)
        embed.add_field(name="記憶體佔用 (%)", value=", ".join(map(str, self.memory_history)), inline=False)
        embed.add_field(name="記憶體使用量 (MB)", value=f"{memory_usage:.2f} / {memory_total:.2f}", inline=False)
        if cpu_highest_process_info:
            embed.add_field(name="最高 CPU 佔用程序", value=f"{cpu_highest_process_info['name']} (PID: {cpu_highest_process_info['pid']}) - {cpu_highest_process_info['cpu_percent']}%", inline=False)
        if memory_highest_process_info:
            embed.add_field(name="最高記憶體佔用程序", value=f"{memory_highest_process_info['name']} (PID: {memory_highest_process_info['pid']}) - {memory_highest_process_info['memory_percent']}%", inline=False)
        await channel.send(embed=embed)

asyncio.run(bot.add_cog(PSMonitor(bot)))

if __name__ == "__main__":
    start_bot()