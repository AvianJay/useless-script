import discord
from discord.ext import commands, tasks
from discord import app_commands
import rynaki
import asyncio
from globalenv import bot
from datetime import datetime, timezone, timedelta

in_game_sessions = {}
last_game_time = {}

@app_commands.allowed_installs(guilds=True, users=True)
@app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
class Aki(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def cog_load(self):
        self.cleanup_task.start()

    async def cog_unload(self):
        self.cleanup_task.cancel()

    @tasks.loop(minutes=10)
    async def cleanup_task(self):
        current_time = datetime.now(timezone.utc)
        to_remove = [user_id for user_id, time in last_game_time.items() if current_time - time > timedelta(minutes=5)]
        for user_id in to_remove:
            last_game_time.pop(user_id, None)

    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.command(name="aki", description="與 Akinator 進行遊戲")
    async def aki_command(self, interaction: discord.Interaction):
        await interaction.response.defer()
        if interaction.user.id in in_game_sessions:
            await interaction.followup.send("你已經有一個進行中的遊戲！請先結束它。", ephemeral=True)
            return
        if datetime.now(timezone.utc) - last_game_time.get(interaction.user.id, datetime.min.replace(tzinfo=timezone.utc)) < timedelta(minutes=1):
            await interaction.followup.send("請稍等 1 分鐘後再開始新的遊戲。", ephemeral=True)
            return
        try:
            in_game_sessions[interaction.user.id] = True
            game = AkinatorGame(interaction)
            await game.start()
        except Exception as e:
            if not interaction.response.is_done():
                await interaction.response.send_message(f"發生錯誤: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"發生錯誤: {e}", ephemeral=True)
            in_game_sessions.pop(interaction.user.id, None)


class AkinatorGame:
    def __init__(self, interaction: discord.Interaction):
        self.interaction = interaction
        self.aki = rynaki.Akinator(lang="cn", theme="characters")
        self.view = AkinatorView(self)
        self.message = None
        self.counter = 0

    async def start(self):
        # Start game in thread to avoid blocking
        question = await asyncio.to_thread(self.aki.start_game)
        embed = self.get_question_embed(question)
        self.message = await self.interaction.followup.send(embed=embed, view=self.view)

    async def post_answer(self, answer):
        await asyncio.to_thread(self.aki.post_answer, answer)
        self.counter += 1
        
        if self.aki.name:
            # Game finished, found a character
            embed = discord.Embed(
                title="Akinator 的猜測",
                description=f"我猜是... **{self.aki.name}**！\n{self.aki.description}",
                color=0x00ff00
            )
            if self.aki.photo:
                embed.set_image(url=self.aki.photo)
            embed.set_footer(text=f"共問了 {self.counter} 個問題。")
            
            # Disable all buttons
            for child in self.view.children:
                child.disabled = True
            
            await self.message.edit(embed=embed, view=self.view)
            last_game_time[self.interaction.user.id] = datetime.now(timezone.utc)
            in_game_sessions.pop(self.interaction.user.id, None)
            self.view.stop()
        else:
            # Continue game
            embed = self.get_question_embed(self.aki.question)
            await self.message.edit(embed=embed, view=self.view)

    def get_question_embed(self, question):
        embed = discord.Embed(title="Akinator", description=question, color=0x3498db)
        embed.set_footer(text=f"進度: {self.aki.progression:.2f}%")
        return embed


class AkinatorView(discord.ui.View):
    def __init__(self, game: AkinatorGame):
        super().__init__(timeout=300)
        self.game = game
    
    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        in_game_sessions.pop(self.game.interaction.user.id, None)
        last_game_time[self.game.interaction.user.id] = datetime.now(timezone.utc)
        await self.game.message.edit(content="遊戲已超時結束。", view=self)
        self.stop()

    async def handle_answer(self, interaction: discord.Interaction, answer: str):
        if interaction.user != self.game.interaction.user:
            await interaction.response.send_message("這不是你的遊戲！", ephemeral=True)
            return

        await interaction.response.defer()
        try:
            await self.game.post_answer(answer)
        except rynaki.main.AkinatorError:
            in_game_sessions.pop(self.game.interaction.user.id, None)
            last_game_time[self.game.interaction.user.id] = datetime.now(timezone.utc)
            await interaction.followup.send("Akinator 服務暫時無法使用，請稍後再試。", ephemeral=True)
            await self.game.message.edit(content="遊戲因錯誤結束。", view=None)
            self.stop()

    @discord.ui.button(label="是", style=discord.ButtonStyle.green)
    async def yes_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "y")

    @discord.ui.button(label="否", style=discord.ButtonStyle.red)
    async def no_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "n")

    @discord.ui.button(label="不知道", style=discord.ButtonStyle.gray)
    async def idk_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "idk")

    @discord.ui.button(label="應該是", style=discord.ButtonStyle.blurple)
    async def probably_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "p")

    @discord.ui.button(label="應該不是", style=discord.ButtonStyle.blurple)
    async def probably_not_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.handle_answer(interaction, "pn")

    @discord.ui.button(label="結束遊戲", style=discord.ButtonStyle.danger, row=1)
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user != self.game.interaction.user:
            await interaction.response.send_message("這不是你的遊戲！", ephemeral=True)
            return
        
        await interaction.response.defer()
        for child in self.children:
            child.disabled = True
        in_game_sessions.pop(self.game.interaction.user.id, None)
        last_game_time[self.game.interaction.user.id] = datetime.now(timezone.utc)
        await self.game.message.edit(content="遊戲已由用戶結束。", view=self)
        self.stop()

asyncio.run(bot.add_cog(Aki(bot)))