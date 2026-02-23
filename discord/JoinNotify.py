from globalenv import bot, get_command_mention, config, set_user_data, get_user_data
import discord
from discord.ext import commands
from discord import app_commands
import asyncio

class JoinNotifyView(discord.ui.View):
    @discord.ui.button(label="官方網站", style=discord.ButtonStyle.link, url=config('website_url'))
    async def website_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="加入支援伺服器", style=discord.ButtonStyle.link, url=config('support_server_invite'))
    async def support_server_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    @discord.ui.button(label="停用加入通知", style=discord.ButtonStyle.secondary, custom_id="dont_notify_join")
    async def dont_notify_join(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="好吧",
            description="我不會再通知你了！如果你改變主意了，可以使用 `/joinnotify` 指令來重新啟用加入通知！",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        set_user_data(0, interaction.user.id, "join_notify", False)

class JoinNotify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        bot.add_view(JoinNotifyView())

    @app_commands.command(name="joinnotify", description="設定是否在你邀請我加入伺服器時私訊")
    @app_commands.choices(option=[
        app_commands.Choice(name="好啊", value="enable"),
        app_commands.Choice(name="不要", value="disable")
    ])
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def joinnotify(self, interaction: discord.Interaction, option: str):
        if option == "enable":
            set_user_data(0, interaction.user.id, "join_notify", True)
            await interaction.response.send_message("已啟用加入通知！當你邀請我加入伺服器時，我會私訊你一個歡迎訊息！", ephemeral=True)
        else:
            set_user_data(0, interaction.user.id, "join_notify", False)
            await interaction.response.send_message("已停用加入通知！當你邀請我加入伺服器時，我將不會私訊你任何訊息！", ephemeral=True)

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        # try to find who invited the bot using the audit logs
        inviter = None
        async for entry in guild.audit_logs(limit=10, action=discord.AuditLogAction.bot_add):
            if entry.target.id == self.bot.user.id:
                inviter = entry.user
                break
        if inviter:
            if not get_user_data(0, inviter.id, "join_notify", True):
                return
            try:
                embed = discord.Embed(
                    title="欸？你好像邀請了我？",
                    description=f"你好啊，{inviter.mention}！感謝你邀請我加入 {guild.name}！\n快速開始：\n- {await get_command_mention('help')} 查看指令列表\n- {await get_command_mention('tutorial')} 查看使用教學\n- {await get_command_mention('panel')} 開啟網頁面板\n如果有任何問題，歡迎加入[支援伺服器]({config('support_server_invite')})尋求幫助！\n\n-# 不想收到這些訊息？你可以使用 {await get_command_mention('joinnotify')} 指令來關閉加入通知！",
                    color=discord.Color.green()
                )
                embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
                view = JoinNotifyView()
                await inviter.send(embed=embed, view=view)
            except discord.Forbidden:
                pass
        else:
            # dm the owner of the guild if we can't find the inviter
            owner = guild.owner
            if not get_user_data(0, owner.id, "join_notify", True):
                return
            try:
                embed = discord.Embed(
                    title="欸？好像有人邀請了我？",
                    description=f"你好啊，{owner.mention}！好像有人把我邀請進入你的伺服器 {guild.name} 了？但是我沒有權限可以知道他是誰 :/\n快速開始：\n- {await get_command_mention('help')} 查看指令列表\n- {await get_command_mention('tutorial')} 查看使用教學\n- {await get_command_mention('panel')} 開啟網頁面板\n如果有任何問題，歡迎加入[支援伺服器]({config('support_server_invite')})尋求幫助！\n\n-# 不想收到這些訊息？你可以使用 {await get_command_mention('joinnotify')} 指令來關閉加入通知！",
                    color=discord.Color.green()
                )
                embed.set_footer(text=guild.name, icon_url=guild.icon.url if guild.icon else None)
                view = JoinNotifyView()
                await owner.send(embed=embed, view=view)
            except discord.Forbidden:
                pass

asyncio.run(bot.add_cog(JoinNotify(bot)))