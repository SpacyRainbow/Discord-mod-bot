"""
poll - /poll with 2-5 button options, one vote per user (clicking a
different option switches it, clicking your current one retracts it).
Auto-closes via the scheduler engine if a duration is given; otherwise
stays open until /pollclose.

Unlike giveaway.py's single shared persistent view (one fixed custom_id
works for every giveaway, since there's only ever one button), a poll's
button count varies per-poll (2-5), so each poll needs its OWN view
instance with custom_ids that embed the poll's row ID
(f"poll:{poll_id}:vote:{i}") - cog_load re-hydrates one persistent view per
still-open poll found in the database, so voting keeps working across a
bot restart.
"""

from __future__ import annotations

import datetime
import json
from typing import Optional

import discord
from discord.ext import commands

from ..durations import parse_duration

MAX_POLL_DURATION = datetime.timedelta(days=30)


def _parse_end_at(end_at: Optional[str]) -> Optional[datetime.datetime]:
    return datetime.datetime.fromisoformat(end_at) if end_at else None


class PollView(discord.ui.View):
    def __init__(self, cog: "Poll", poll_id: int, options: list):
        super().__init__(timeout=None)
        self.cog = cog
        for index, option in enumerate(options):
            button = discord.ui.Button(
                label=option[:80], custom_id=f"poll:{poll_id}:vote:{index}", style=discord.ButtonStyle.primary
            )
            button.callback = self._make_callback(poll_id, index)
            self.add_item(button)

    def _make_callback(self, poll_id: int, option_index: int):
        async def callback(interaction: discord.Interaction):
            await self.cog.handle_vote(interaction, poll_id, option_index)

        return callback


class Poll(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.scheduler_handlers["poll_close"] = self._handle_poll_close
        for row in await self.bot.stores.polls.open_polls():
            poll_id, _, _, message_id, _, options_json, _, _ = row
            if not message_id:
                continue
            self.bot.add_view(PollView(self, poll_id, json.loads(options_json)), message_id=message_id)

    def _build_embed(
        self, question: str, options: list, counts: list, end_at: Optional[datetime.datetime]
    ) -> discord.Embed:
        total = sum(counts) or 1
        lines = []
        for index, option in enumerate(options):
            count = counts[index] if index < len(counts) else 0
            pct = round(100 * count / total)
            filled = round(pct / 10)
            bar = "\N{FULL BLOCK}" * filled + "\N{LIGHT SHADE}" * (10 - filled)
            lines.append(f"**{option}**\n{bar} {count} ({pct}%)")
        description = "\n\n".join(lines)
        if end_at:
            description += f"\n\nCloses {discord.utils.format_dt(end_at, 'R')}"
        return discord.Embed(title=question, description=description, color=discord.Color.blurple())

    async def handle_vote(self, interaction: discord.Interaction, poll_id: int, option_index: int) -> None:
        row = await self.bot.stores.polls.get(poll_id)
        if row is None:
            await interaction.response.send_message("Couldn't find this poll.", ephemeral=True)
            return
        _, _, _, _, question, options_json, end_at, closed = row
        if closed:
            await interaction.response.send_message("This poll is closed.", ephemeral=True)
            return

        await self.bot.stores.polls.set_vote(poll_id, interaction.user.id, option_index)
        options = json.loads(options_json)
        counts = await self.bot.stores.polls.vote_counts(poll_id, len(options))
        embed = self._build_embed(question, options, counts, _parse_end_at(end_at))
        await interaction.response.edit_message(embed=embed)

    async def _handle_poll_close(self, guild_id: int, payload: dict) -> None:
        await self._close_poll(payload["poll_id"])

    async def _close_poll(self, poll_id: int) -> None:
        row = await self.bot.stores.polls.get(poll_id)
        if row is None:
            return
        _, guild_id, channel_id, message_id, question, options_json, end_at, closed = row
        if closed:
            return
        await self.bot.stores.polls.close(poll_id)

        options = json.loads(options_json)
        counts = await self.bot.stores.polls.vote_counts(poll_id, len(options))
        embed = self._build_embed(question, options, counts, _parse_end_at(end_at))
        embed.title = f"[Closed] {question}"

        guild = self.bot.get_guild(guild_id)
        channel = guild.get_channel(channel_id) if guild is not None else None
        if channel is not None and message_id:
            try:
                message = await channel.fetch_message(message_id)
                await message.edit(embed=embed, view=None)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                pass

    @commands.hybrid_command(name="poll", description="Start a poll with 2-5 options")
    @commands.guild_only()
    async def poll(
        self,
        ctx: commands.Context,
        question: str,
        option1: str,
        option2: str,
        option3: Optional[str] = None,
        option4: Optional[str] = None,
        option5: Optional[str] = None,
        duration: Optional[str] = None,
    ):
        options = [o for o in (option1, option2, option3, option4, option5) if o]
        if len(options) < 2:
            await ctx.send("Give at least two options.")
            return

        end_at = None
        if duration:
            try:
                end_at = discord.utils.utcnow() + parse_duration(duration, max_duration=MAX_POLL_DURATION)
            except ValueError as e:
                await ctx.send(str(e))
                return

        try:
            poll_id = await self.bot.stores.polls.create(
                ctx.guild.id, ctx.channel.id, 0, question, options, end_at
            )
        except RuntimeError:
            await ctx.send("Couldn't save this poll (database unavailable).")
            return

        embed = self._build_embed(question, options, [0] * len(options), end_at)
        view = PollView(self, poll_id, options)
        message = await ctx.send(embed=embed, view=view)
        await self.bot.stores.polls.set_message_id(poll_id, message.id)
        if end_at:
            await self.bot.stores.scheduled.add(ctx.guild.id, "poll_close", {"poll_id": poll_id}, end_at)

    @commands.hybrid_command(name="pollclose", description="Close a poll early")
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def poll_close_cmd(self, ctx: commands.Context, message_id: int):
        row = await self.bot.stores.polls.get_by_message(ctx.guild.id, message_id)
        if row is None:
            await ctx.send("No poll found for that message ID.")
            return
        if row[7]:
            await ctx.send("That poll is already closed.")
            return
        await self._close_poll(row[0])
        await ctx.send("Poll closed.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Poll(bot))
