"""
starboard - a message that collects enough star reactions gets reposted in
a dedicated channel. Once posted, a starboard entry is never un-posted if
the star count later drops back under the threshold (only the displayed
count updates) - this matches the common behavior across other starboard
bots, and avoids the surprise of a message vanishing from the starboard
after someone changes their mind about a reaction.

A message's own author starring their own message never counts towards
the threshold. Reactions inside the starboard channel itself are ignored,
so a starboard post can't recursively star itself onto the starboard.

Config keys:
  starboard.channel   - channel ID to post to (0/unset = disabled)
  starboard.threshold - stars needed before posting, default 3
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands, tasks

logger = logging.getLogger("bot.modules.starboard")

STAR_EMOJI = "\N{WHITE MEDIUM STAR}"

LOCK_SWEEP_MINUTES = 10


def star_line(count: int) -> str:
    return f"{STAR_EMOJI} **{count}**"


class Starboard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # One lock per (guild_id, message_id), held across the check-post-record
        # sequence in _handle_reaction. The store's INSERT OR REPLACE stops the
        # crash, but only this stops two reactions crossing the threshold together
        # from both posting a duplicate to the starboard. (review F9)
        # Swept by sweep_locks below - one entry per distinct starred message
        # otherwise accumulates forever. (review F10)
        self._locks: dict[tuple[int, int], asyncio.Lock] = {}

    def _lock_for(self, guild_id: int, message_id: int) -> asyncio.Lock:
        return self._locks.setdefault((guild_id, message_id), asyncio.Lock())

    async def cog_load(self) -> None:
        self.sweep_locks.start()

    def cog_unload(self):
        self.sweep_locks.cancel()

    def _sweep_locks(self) -> int:
        """Drops every lock that isn't currently held. An asyncio.Lock can only
        have waiters while it is locked, so an unheld lock is unreachable state
        and safe to drop - but evicting a HELD one would hand the holder and the
        next caller two different Lock objects for the same key and silently
        undo the mutual exclusion. (review F9/F10)"""
        dropped = 0
        for key, lock in list(self._locks.items()):
            if lock.locked():
                continue
            self._locks.pop(key, None)
            dropped += 1
        return dropped

    @tasks.loop(minutes=LOCK_SWEEP_MINUTES)
    async def sweep_locks(self):
        # tasks.loop only auto-restarts on network errors; anything else would stop
        # this loop permanently, so nothing may escape the body. (review F4)
        try:
            self._sweep_locks()
        except Exception:
            logger.exception("starboard sweep_locks iteration failed")

    @sweep_locks.before_loop
    async def before_sweep_locks(self):
        await self.bot.wait_until_ready()

    @commands.hybrid_command(
        name="setstarboard", description="Set the channel (and star threshold) for the starboard"
    )
    @commands.has_permissions(manage_guild=True)
    async def set_starboard(self, ctx: commands.Context, channel: discord.TextChannel, threshold: int = 3):
        if threshold < 1:
            await ctx.send("Threshold must be at least 1.")
            return
        await self.bot.stores.config.set(ctx.guild.id, "starboard.channel", str(channel.id))
        await self.bot.stores.config.set(ctx.guild.id, "starboard.threshold", str(threshold))
        await ctx.send(f"Messages with {threshold}+ stars will be posted in {channel.mention}.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        await self._handle_reaction(payload)

    async def _handle_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None or str(payload.emoji) != STAR_EMOJI:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        starboard_channel_id = await self.bot.stores.config.get_int(guild.id, "starboard.channel", 0)
        if not starboard_channel_id or payload.channel_id == starboard_channel_id:
            return
        starboard_channel = guild.get_channel(starboard_channel_id)
        if starboard_channel is None:
            return

        source_channel = guild.get_channel(payload.channel_id)
        if source_channel is None:
            return
        try:
            message = await source_channel.fetch_message(payload.message_id)
        except (discord.NotFound, discord.Forbidden):
            return

        # The whole check-post-record sequence is serialised per message, so a
        # second reaction can't read "not posted yet" while the first is still
        # awaiting its send() and post a duplicate. (review F9)
        async with self._lock_for(guild.id, message.id):
            star_count = await self._count_stars(message)
            existing_id = await self.bot.stores.starboard.get(guild.id, message.id)

            if existing_id is not None:
                try:
                    starboard_message = await starboard_channel.fetch_message(existing_id)
                    await starboard_message.edit(content=star_line(star_count))
                except (discord.NotFound, discord.Forbidden):
                    pass
                return

            # 1-100 matches set_starboard, which already rejects threshold < 1.
            # A stored 0 would star every message with no stars. (review F14)
            threshold = await self.bot.stores.config.get_int(
                guild.id, "starboard.threshold", 3, minimum=1, maximum=100
            )
            if star_count < threshold:
                return

            embed = self._build_embed(message)
            try:
                posted = await starboard_channel.send(content=star_line(star_count), embed=embed)
            except discord.Forbidden:
                logger.warning("Missing permission to post in starboard channel %s", starboard_channel_id)
                return
            try:
                await self.bot.stores.starboard.set(guild.id, message.id, posted.id)
            except RuntimeError:
                pass  # DB unavailable - the post itself still went out fine

    async def _count_stars(self, message: discord.Message) -> int:
        for reaction in message.reactions:
            if str(reaction.emoji) != STAR_EMOJI:
                continue
            count = reaction.count
            async for user in reaction.users():
                if user.id == message.author.id:
                    count -= 1
                    break
            return max(count, 0)
        return 0

    def _build_embed(self, message: discord.Message) -> discord.Embed:
        embed = discord.Embed(
            description=message.content or "*(no text content)*",
            color=discord.Color.gold(),
            timestamp=message.created_at,
        )
        embed.set_author(name=str(message.author), icon_url=message.author.display_avatar.url)
        embed.add_field(name="Source", value=f"[Jump to message]({message.jump_url})", inline=False)
        if message.attachments:
            embed.set_image(url=message.attachments[0].url)
        return embed


async def setup(bot: commands.Bot):
    await bot.add_cog(Starboard(bot))
