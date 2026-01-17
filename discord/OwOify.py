from globalenv import bot
import discord
from discord import app_commands
from discord.ext import commands
import re
import random
from owoify import owoify
import asyncio

def owoify_chinese(text):
    # 1. 來自圖片中的前綴與後綴清單
    prefixes = ["OwO", "嘿嘿", "UwU", "(*^ω^*)", "(｡♥‿♥｡)", "ʕ•́ᴥ•̀ʔっ", "ヽ(・∀・)ﾉ", "(≧◡≦)"]
    suffixes = [
        ":3", ">:3", "xox", ">3<", "UwU", "嘿嘿", "ʕʘ‿ʘʔ", "( • ω • )", 
        "(> ◡ <)", "~", "(*≧▽≦)", "( ° ∀ ° )", "( • ◡ • )", 
        "(￣▽￣)", "( ` ω ´ )", "(/ = 3 =)/", "╰(*´︶`*)╯", "喵~"
    ]

    # 2. 中文語氣替換
    replacements = {
        "了": [
            "惹",
            "ㄌ",
            # "惹喵"  # 了喵 會讓句尾重複出現喵，先拿掉
        ],
        "的": ["噠", "ㄉ"],
        "我": ["偶"],
        "你": ["泥"],
        "妳": ["妮"],
        "您": ["尼"],
        "嗎": ["喵？", "咩？"],
        "吧": ["喵~"],
        "！": [
            "！ >w<",
            "！ (≧◡≦)",
            "喵！"
        ],
        "不要": [
            "別要喵",
            "補藥"
        ],
        "謝謝": [
            "謝謝喵",
            "謝謝泥喵",
            "謝、謝謝泥"
        ],
        "真的很": [
            "超、超超級...的",
        ],
        "是不是": [
            "係咪",
            "是咪",
            "4不4",
            "是不是呀"
        ],
        "所以": [
            "所、所以說",
            "所、所以喵"
        ],
        "因為": [
            "因、因為",
            "因、因為喵"
        ],
        "知道": [
            "知、知道喵",
            "知道惹",
            "知道ㄌ",
            "造惹"
        ],
        "這樣": [
            "這樣子喵",
            "醬"
        ]
    }
    for old, candidates in replacements.items():
        if old in text:
            # 這裡我們用正則或簡單 replace，隨機選一個
            text = text.replace(old, random.choice(candidates))

    # 口吃邏輯 (針對中文，split() 可能會切不開，建議直接對字串處理)
    if random.random() < 0.20:
        if len(text) > 2:
            idx = random.randint(0, len(text)-1)
            text = text[:idx] + f"{text[idx]}-{text[idx]}" + text[idx+1:]

    if random.random() < 0.40:
        text = f"{random.choice(prefixes)} {text}"
    if random.random() < 0.40:
        text = f"{text} {random.choice(suffixes)}"
    return text


class OwOify(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.ctx_menu = app_commands.ContextMenu(
            name="OwOify",
            callback=self.owoify_context_menu
        )
        bot.tree.add_command(self.ctx_menu)

    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    @app_commands.command(name="owoify", description="文字也能變可愛！")
    @app_commands.describe(text="要變可愛的文字", english="是否英文版？")
    async def owoify(self, interaction: discord.Interaction, text: str, english: bool = False):
        if english:
            owoified_text = owoify(text)
        else:
            owoified_text = owoify_chinese(text)
        embed = discord.Embed(description=owoified_text, color=0xffc0cb)
        embed.set_author(name=f"{interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
        await interaction.response.send_message(embed=embed)
    
    @commands.command(name="owoify", help="文字也能變可愛！", aliases=["owo"])
    async def owoify_command(self, ctx: commands.Context, *, text: str = ""):
        """把文字變可愛！
        用法: owoify [--english] <text> 或回覆一則訊息使用 owoify [--english]
        
        --english: 使用英文版 owoify
        """
        author = ctx.author
        english = False
        if "--english" in text:
            text = text.replace("--english", "").strip()
            english = True
        if not text:
            if not ctx.message.reference:
                await ctx.reply("請提供要變可愛的文字，或回覆一則訊息來變可愛該訊息的內容。")
                return
            try:
                referenced_message = await ctx.channel.fetch_message(ctx.message.reference.message_id)
                text = referenced_message.content
                author = referenced_message.author
            except Exception:
                await ctx.reply("無法取得回覆的訊息內容，請確保該訊息存在且可讀取。")
                return
        if not text:
            await ctx.reply("回覆的訊息沒有內容可供 OwOify。")
            return
        if english:
            owoified_text = owoify(text)
        else:
            owoified_text = owoify_chinese(text)
        embed = discord.Embed(description=owoified_text, color=0xffc0cb)
        embed.set_author(name=f"{author.display_name}", icon_url=author.display_avatar.url)
        embed.set_footer(text="owoify")
        await ctx.reply(embed=embed)
        
    # content menu
    @app_commands.allowed_contexts(guilds=True, dms=True, private_channels=True)
    @app_commands.allowed_installs(guilds=True, users=True)
    async def owoify_context_menu(self, interaction: discord.Interaction, message: discord.Message):
        if not message.content:
            await interaction.response.send_message("該訊息沒有內容可供 OwOify。", ephemeral=True)
            return
        owoified_text = owoify_chinese(message.content)
        embed = discord.Embed(description=owoified_text, color=0xffc0cb)
        embed.set_author(name=f"{message.author.display_name}", icon_url=message.author.display_avatar.url)
        embed.set_footer(text="owoify")
        await interaction.response.send_message(embed=embed)

asyncio.run(bot.add_cog(OwOify(bot)))