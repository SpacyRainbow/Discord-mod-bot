from __future__ import annotations

import discord
from discord.ext import commands


class Quote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="addquote")
    async def add_quote(self, ctx: commands.Context, author: str, *, content: str):
        quote_id = await self.bot.stores.quotes.add(ctx.guild.id, author, content, ctx.author.id)
        await ctx.send(f"Saved as quote #{quote_id}.")

    @commands.command(name="quote")
    async def quote(self, ctx: commands.Context, quote_id: int | None = None):
        if quote_id is None:
            row = await self.bot.stores.quotes.random(ctx.guild.id)
        else:
            row = await self.bot.stores.quotes.get(ctx.guild.id, quote_id)
        if row is None:
            await ctx.send("No quotes saved yet. Add one with `!addquote <author> <text>`.")
            return
        qid, author, content = row
        embed = discord.Embed(description=content)
        embed.set_footer(text=f"— {author} (#{qid})")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Quote(bot))
