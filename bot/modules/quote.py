from __future__ import annotations

import re
from typing import Optional

import discord
from discord.ext import commands

# Matches raw <@id>/<@!id>/<@&id>/<#id> mention markup as stored verbatim in
# quote text (e.g. typed into the `author` field, or pasted from a message).
# Embed footers - where the author is shown - never render mention markup,
# so without resolving these first a quote about someone mentioned by @ just
# displays the raw "<@123...>" text instead of their name.
_MENTION_RE = re.compile(r"<(@!?|@&|#)(\d+)>")


def _resolve_mentions(guild: Optional[discord.Guild], text: str) -> str:
    """Pure-ish (given a guild): replaces raw mention markup with a plain,
    readable @name/@role/#channel, falling back to the original markup for
    anything no longer resolvable (e.g. someone who's since left)."""
    if guild is None or not text:
        return text

    def replace(match: "re.Match[str]") -> str:
        prefix, obj_id = match.group(1), int(match.group(2))
        if prefix == "@&":
            role = guild.get_role(obj_id)
            return f"@{role.name}" if role else match.group(0)
        if prefix == "#":
            channel = guild.get_channel(obj_id)
            return f"#{channel.name}" if channel else match.group(0)
        member = guild.get_member(obj_id)
        return f"@{member.display_name}" if member else match.group(0)

    return _MENTION_RE.sub(replace, text)


class Quote(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="addquote")
    async def add_quote(self, ctx: commands.Context, author: str, *, content: str):
        quote_id = await self.bot.stores.quotes.add(ctx.guild.id, author, content, ctx.author.id)
        await ctx.send(f"Saved as quote #{quote_id}.")

    @commands.hybrid_command(name="quote")
    async def quote(self, ctx: commands.Context, quote_id: Optional[int] = None):
        # Discord invalidates a slash command's interaction ~3s after
        # dispatch if nothing's responded yet - deferring immediately trades
        # that for the ~15 minute followup-webhook window, so a DB query
        # queued up behind a burst of other commands doesn't blow past it.
        await ctx.defer()
        if quote_id is None:
            row = await self.bot.stores.quotes.random(ctx.guild.id)
        else:
            row = await self.bot.stores.quotes.get(ctx.guild.id, quote_id)
        if row is None:
            await ctx.send("No quotes saved yet. Add one with `!addquote <author> <text>`.")
            return
        qid, author, content = row
        author = _resolve_mentions(ctx.guild, author)
        content = _resolve_mentions(ctx.guild, content)
        embed = discord.Embed(description=content)
        embed.set_footer(text=f"— {author} (#{qid})")
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Quote(bot))
