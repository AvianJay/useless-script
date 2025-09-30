# Doomcord
# uses PortalRunner's Doomcord (https://doom.p2r3.com/)
import discord
from globalenv import bot, start_bot


def generate_doom_embed(link="https://doom.p2r3.com/i.webp", step=None):
    embed = discord.Embed(color=0xff0000)
    # title="DOOM", 
    if step:
        # verify
        if step not in ["w", "a", "s", "d", "q", "e"]:
            raise ValueError("Invalid step")
        link = link.replace("i", step + "i")
    embed.set_image(url=link)
    embed.set_footer(text="Doomcord by PortalRunner", icon_url="https://yt3.ggpht.com/ytc/AIdro_mWb-zYQYCfaIC0pRsxHQqxQiIIpDLXOhB1YZXPgMKGsQ=s68-c-k-c0x00ffffff-no-rj")
    # embed.set_author(name="Doomcord", icon_url="https://yt3.ggpht.com/ytc/AIdro_mWb-zYQYCfaIC0pRsxHQqxQiIIpDLXOhB1YZXPgMKGsQ=s68-c-k-c0x00ffffff-no-rj")
    return embed, link

async def update_doom(interaction: discord.Interaction, step: str, link="https://doom.p2r3.com/i.webp"):
    embed, link = generate_doom_embed(step=step)
    await interaction.response.edit_message(embed=embed)
    return link


@bot.tree.command(name="doom", description="開始玩 DOOM")
async def doom_command(interaction: discord.Interaction):
    link = "https://doom.p2r3.com/i.webp"

    class StepButton(discord.ui.Button):
        def __init__(self, step, label=None, emoji=None, style=discord.ButtonStyle.primary, row: int = 0):
            # label=label or step.upper(), 
            super().__init__(style=style, row=row, emoji=emoji)
            self.step = step

        async def callback(self, interaction: discord.Interaction):
            nonlocal link
            # update link and embed using the current link + chosen step
            embed, link = generate_doom_embed(link=link, step=self.step)
            # edit the original message with the new embed (keep the same view)
            await interaction.response.edit_message(embed=embed, view=self.view)

    # initial embed (if a step was provided when running the command)
    embed, link = generate_doom_embed()

    emoji_map = {
        "q": "🔫",
        "w": "⬆️",
        "e": "🖐️",
        "a": "⬅️",
        "s": "⬇️",
        "d": "➡️",
    }

    view = discord.ui.View(timeout=None)
    for i, s in enumerate(["q", "w", "e", "a", "s", "d"]):
        row = 0 if i < 3 else 1  # put a, s, d on the second row (row index 1)
        view.add_item(StepButton(step=s, emoji=emoji_map.get(s), row=row))

    await interaction.response.send_message(embed=embed, view=view)
    

if __name__ == "__main__":
    start_bot()
