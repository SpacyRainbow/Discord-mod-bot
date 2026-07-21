"""
giveaway - /giveaway start/end/reroll. One persistent "Enter" button per
giveaway message; ending happens through the scheduler engine (see
scheduler.py) so it survives a bot restart mid-giveaway.

Persistence pattern: GiveawayView's button has a fixed custom_id
("giveaway:enter") instead of the decorator's auto-generated one, and
cog_load registers ONE fallback instance via bot.add_view(...) with no
message_id - that's what makes the button still work after a restart, even
though the specific view object that was attached when the message was
first sent is long gone. The button's callback looks up which giveaway a
click belongs to via interaction.message.id, not through any state closed
over at creation time - the standard discord.py "dynamic persistent view"
shape. Clicking again toggles your entry off (Enter/Leave), rather than
needing a separate command to back out.
"""

from __future__ import annotations

import datetime
import random

import discord
from discord.ext import commands

from ..durations import parse_duration

MAX_GIVEAWAY_DURATION = datetime.timedelta(days=30)
MAX_WINNERS = 20


class GiveawayView(discord.ui.View):
    def __init__(self, cog: "Giveaway"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(label="Enter", emoji="\N{PARTY POPPER}", custom_id="giveaway:enter")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if interaction.guild_id is None or interaction.message is None:
            return
        row = await self.cog.bot.stores.giveaways.get_by_message(interaction.guild_id, interaction.message.id)
        if row is None:
            await interaction.followup.send("Couldn't find this giveaway.", ephemeral=True)
            return
        giveaway_id, _, _, _, prize, _, _, _, ended = row
        if ended:
            await interaction.followup.send("This giveaway has ended.", ephemeral=True)
            return
        entered = await self.cog.bot.stores.giveaways.toggle_entry(giveaway_id, interaction.user.id)
        if entered:
            await interaction.followup.send(f"You're entered for **{prize}**!", ephemeral=True)
        else:
            await interaction.followup.send(f"You left the giveaway for **{prize}**.", ephemeral=True)


class Giveaway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(GiveawayView(self))
        self.bot.scheduler_handlers["giveaway_end"] = self._handle_giveaway_end

    def _build_embed(
        self, prize: str, winner_count: int, end_at: datetime.datetime, host: discord.abc.User
    ) -> discord.Embed:
        ends = discord.utils.format_dt(end_at, "R")
        return discord.Embed(
            title="\N{PARTY POPPER} Giveaway",
            description=(
                f"**{prize}**\nWinners: {winner_count}\nEnds: {ends}\nHosted by {host.mention}\n\n"
                "Click Enter below to join!"
            ),
            color=discord.Color.blurple(),
        )

    async def _handle_giveaway_end(self, guild_id: int, payload: dict) -> None:
        await self._finish_giveaway(payload["giveaway_id"], reroll=False)

    async def _finish_giveaway(self, giveaway_id: int, reroll: bool) -> None:
        row = await self.bot.stores.giveaways.get(giveaway_id)
        if row is None:
            return
        _, guild_id, channel_id, message_id, prize, winner_count, _, _, ended = row
        if ended and not reroll:
            return  # already finished - a manual /giveaway end raced the scheduler

        entries = await self.bot.stores.giveaways.entries(giveaway_id)
        winners = random.sample(entries, k=min(winner_count, len(entries))) if entries else []

        if winners:
            winner_text = ", ".join(f"<@{w}>" for w in winners)
            announcement = f"\N{PARTY POPPER} Congratulations {winner_text}! You won **{prize}**!"
            result_line = f"Winner(s): {winner_text}"
        else:
            announcement = f"No valid entries for **{prize}** - no winner this time."
            result_line = "No valid entries."

        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild is not None else None
        if channel is not None:
            try:
                if message_id:
                    message = await channel.fetch_message(message_id)
                    embed = message.embeds[0] if message.embeds else discord.Embed(title=prize)
                    embed.description = f"**Ended.** {result_line}"
                    await message.edit(embed=embed, view=None)
                await channel.send(announcement)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

        if not reroll:
            await self.bot.stores.giveaways.mark_ended(giveaway_id)

    @commands.hybrid_group(name="giveaway", description="Manage giveaways")
    @commands.guild_only()
    async def giveaway_group(self, ctx: commands.Context):
        if ctx.invoked_subcommand is None:
            await ctx.send("Use `/giveaway start`, `/giveaway end`, or `/giveaway reroll`.")

    @giveaway_group.command(name="start", description="Start a giveaway")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_start(self, ctx: commands.Context, duration: str, winners: int, *, prize: str):
        if not 1 <= winners <= MAX_WINNERS:
            await ctx.send(f"Winners must be between 1 and {MAX_WINNERS}.")
            return
        try:
            delta = parse_duration(duration, max_duration=MAX_GIVEAWAY_DURATION)
        except ValueError as e:
            await ctx.send(str(e))
            return

        end_at = discord.utils.utcnow() + delta
        embed = self._build_embed(prize, winners, end_at, ctx.author)
        message = await ctx.send(embed=embed, view=GiveawayView(self))

        try:
            giveaway_id = await self.bot.stores.giveaways.create(
                ctx.guild.id, ctx.channel.id, message.id, prize, winners, ctx.author.id, end_at
            )
            await self.bot.stores.scheduled.add(
                ctx.guild.id, "giveaway_end", {"giveaway_id": giveaway_id}, end_at
            )
        except RuntimeError:
            await message.edit(
                content="Couldn't save this giveaway (database unavailable) - it won't auto-end.",
                view=None,
            )

    @giveaway_group.command(name="end", description="End a giveaway early")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_end_cmd(self, ctx: commands.Context, message_id: int):
        row = await self.bot.stores.giveaways.get_by_message(ctx.guild.id, message_id)
        if row is None:
            await ctx.send("No giveaway found for that message ID.")
            return
        if row[8]:
            await ctx.send("That giveaway has already ended.")
            return
        await self._finish_giveaway(row[0], reroll=False)
        await ctx.send("Giveaway ended.")

    @giveaway_group.command(name="reroll", description="Pick a new winner for an ended giveaway")
    @commands.has_permissions(manage_guild=True)
    async def giveaway_reroll(self, ctx: commands.Context, message_id: int):
        row = await self.bot.stores.giveaways.get_by_message(ctx.guild.id, message_id)
        if row is None:
            await ctx.send("No giveaway found for that message ID.")
            return
        if not row[8]:
            await ctx.send("That giveaway hasn't ended yet - use `/giveaway end` first.")
            return
        await self._finish_giveaway(row[0], reroll=True)
        await ctx.send("Rerolled.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
