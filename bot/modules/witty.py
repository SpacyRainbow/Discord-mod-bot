from __future__ import annotations

import discord
from discord.ext import commands


class Witty(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if self.bot.user not in message.mentions:
            return
        # aguiliar.py listens for the same trigger - a plain @mention. When the
        # LLM is switched on for this guild it owns pings, and a canned witty
        # line fired alongside a real answer just looks broken.
        if await self.bot.stores.config.get_bool(message.guild.id, "llm.enabled", False):
            return
        response = await self.bot.stores.witty.random(message.guild.id)
        if response:
            await message.channel.send(response)

    @commands.hybrid_command(name="wittyadd")
    @commands.has_permissions(manage_guild=True)
    async def witty_add(self, ctx: commands.Context, *, response: str):
        await self.bot.stores.witty.add(ctx.guild.id, response)
        await ctx.send("Added.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Witty(bot))
