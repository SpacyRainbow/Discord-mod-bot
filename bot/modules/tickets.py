"""
tickets - /ticketpanel posts a persistent "Open Ticket" button; clicking it
creates a private text channel visible only to the opener and anyone with
Manage Guild, with its own "Close Ticket" button. One open ticket per user
at a time - opening a second one just points back at the existing channel
instead of creating a duplicate.

Both buttons are single, static persistent views (fixed custom_ids
"tickets:open"/"tickets:close", registered once in cog_load) - unlike
poll.py's per-poll views, there's nothing instance-specific about which
button was clicked: "open" always means "create me a new one" and "close"
resolves the ticket via the channel the click happened in, not anything
encoded in the button itself.

Config keys:
  tickets.category_id - category to create ticket channels under
                         (0/unset = create at the top level instead)
"""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

logger = logging.getLogger("bot.modules.tickets")

CLOSE_DELAY_SECONDS = 5


class TicketPanelView(discord.ui.View):
    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Open Ticket",
        emoji="\N{TICKET}",
        style=discord.ButtonStyle.blurple,
        custom_id="tickets:open",
    )
    async def open_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.open_ticket(interaction)


class TicketCloseView(discord.ui.View):
    def __init__(self, cog: "Tickets"):
        super().__init__(timeout=None)
        self.cog = cog

    @discord.ui.button(
        label="Close Ticket",
        emoji="\N{LOCK}",
        style=discord.ButtonStyle.danger,
        custom_id="tickets:close",
    )
    async def close_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.cog.close_ticket(interaction)


class Tickets(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(TicketPanelView(self))
        self.bot.add_view(TicketCloseView(self))

    @commands.hybrid_command(
        name="ticketpanel", description="Post a button members can click to open a support ticket"
    )
    @commands.guild_only()
    @commands.has_permissions(manage_guild=True)
    async def ticketpanel(self, ctx: commands.Context, *, message: str = "Click below to open a ticket."):
        embed = discord.Embed(title="Support", description=message, color=discord.Color.blurple())
        await ctx.send(embed=embed, view=TicketPanelView(self))

    async def open_ticket(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if guild is None:
            return

        existing = await self.bot.stores.tickets.get_open_for_user(guild.id, interaction.user.id)
        if existing is not None:
            channel = guild.get_channel(existing[1])
            mention = channel.mention if channel else "your existing ticket"
            await interaction.followup.send(f"You already have an open ticket: {mention}", ephemeral=True)
            return

        category_id = await self.bot.stores.config.get_int(guild.id, "tickets.category_id", 0)
        category = guild.get_channel(category_id) if category_id else None
        if not isinstance(category, discord.CategoryChannel):
            category = None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            interaction.user: discord.PermissionOverwrite(view_channel=True, send_messages=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True),
        }
        for role in guild.roles:
            if role.permissions.manage_guild:
                overwrites[role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

        safe_name = "".join(c for c in interaction.user.name.lower() if c.isalnum() or c == "-")
        channel_name = f"ticket-{safe_name or 'user'}"[:90]

        try:
            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                overwrites=overwrites,
                reason=f"Ticket opened by {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send("I don't have permission to create channels.", ephemeral=True)
            return

        try:
            await self.bot.stores.tickets.create(guild.id, channel.id, interaction.user.id)
        except RuntimeError:
            pass  # channel's still usable, just not tracked for the duplicate-ticket check

        embed = discord.Embed(
            title="Support Ticket",
            description=(
                f"{interaction.user.mention}, a mod will be with you shortly. "
                "Click below to close this when you're done."
            ),
            color=discord.Color.blurple(),
        )
        await channel.send(embed=embed, view=TicketCloseView(self))
        await interaction.followup.send(f"Ticket opened: {channel.mention}", ephemeral=True)

    async def close_ticket(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or channel is None:
            return

        ticket = await self.bot.stores.tickets.get_by_channel(guild.id, channel.id)
        if ticket is None:
            await interaction.followup.send("This isn't an open ticket channel.", ephemeral=True)
            return

        try:
            await self.bot.stores.tickets.close(guild.id, channel.id)
        except RuntimeError:
            pass

        await interaction.followup.send(f"Closing this ticket in {CLOSE_DELAY_SECONDS} seconds...")
        await asyncio.sleep(CLOSE_DELAY_SECONDS)
        try:
            await channel.delete(reason=f"Ticket closed by {interaction.user}")
        except (discord.NotFound, discord.Forbidden):
            pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Tickets(bot))
