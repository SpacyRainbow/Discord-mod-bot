"""
roles - self-assignable roles, reaction roles, and auto-role on join.

Config keys:
  roles.autorole - role ID to grant automatically when a member joins (0/unset = off)
"""

from __future__ import annotations

import discord
from discord.ext import commands


class Roles(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- auto-role on join ----

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        role_id = await self.bot.stores.config.get_int(member.guild.id, "roles.autorole", 0)
        if not role_id:
            return
        role = member.guild.get_role(role_id)
        if role is not None:
            try:
                await member.add_roles(role, reason="Auto-role on join")
            except discord.Forbidden:
                pass

    @commands.command(name="setautorole")
    @commands.has_permissions(manage_roles=True)
    async def set_autorole(self, ctx: commands.Context, role: discord.Role):
        await self.bot.stores.config.set(ctx.guild.id, "roles.autorole", str(role.id))
        await ctx.send(f"New members will automatically get {role.mention}.")

    # ---- self-assignable roles ----

    @commands.command(name="allowrole")
    @commands.has_permissions(manage_roles=True)
    async def allow_role(self, ctx: commands.Context, role: discord.Role):
        """Mark a role as self-assignable via !iam."""
        await self.bot.stores.roles.add_self_assignable(ctx.guild.id, role.id)
        await ctx.send(f"{role.mention} can now be self-assigned with `!iam {role.name}`.")

    @commands.command(name="disallowrole")
    @commands.has_permissions(manage_roles=True)
    async def disallow_role(self, ctx: commands.Context, role: discord.Role):
        await self.bot.stores.roles.remove_self_assignable(ctx.guild.id, role.id)
        await ctx.send(f"{role.mention} is no longer self-assignable.")

    @commands.command(name="iam")
    async def iam(self, ctx: commands.Context, *, role: discord.Role):
        allowed = await self.bot.stores.roles.list_self_assignable(ctx.guild.id)
        if role.id not in allowed:
            await ctx.send(f"{role.name} isn't self-assignable.")
            return
        await ctx.author.add_roles(role, reason="Self-assigned via !iam")
        await ctx.send(f"Gave you {role.mention}.")

    @commands.command(name="iamnot")
    async def iamnot(self, ctx: commands.Context, *, role: discord.Role):
        allowed = await self.bot.stores.roles.list_self_assignable(ctx.guild.id)
        if role.id not in allowed:
            await ctx.send(f"{role.name} isn't a self-assignable role.")
            return
        await ctx.author.remove_roles(role, reason="Self-removed via !iamnot")
        await ctx.send(f"Removed {role.mention}.")

    # ---- reaction roles ----

    @commands.command(name="reactionrole")
    @commands.has_permissions(manage_roles=True)
    async def reaction_role(
        self, ctx: commands.Context, message_id: int, emoji: str, role: discord.Role
    ):
        """Bind an emoji reaction on an existing message to a role.
        Usage: !reactionrole <message_id> <emoji> @role
        The bot must be able to see the target message in this channel."""
        try:
            message = await ctx.channel.fetch_message(message_id)
        except discord.NotFound:
            await ctx.send("Couldn't find that message in this channel.")
            return
        await message.add_reaction(emoji)
        await self.bot.stores.roles.add_reaction_role(ctx.guild.id, message_id, emoji, role.id)
        await ctx.send(f"Reacting with {emoji} on that message now grants {role.mention}.")

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None or payload.member is None or payload.member.bot:
            return
        role_id = await self.bot.stores.roles.get_reaction_role(
            payload.guild_id, payload.message_id, str(payload.emoji)
        )
        if role_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        role = guild.get_role(role_id) if guild else None
        if role is not None:
            try:
                await payload.member.add_roles(role, reason="Reaction role")
            except discord.Forbidden:
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.guild_id is None:
            return
        role_id = await self.bot.stores.roles.get_reaction_role(
            payload.guild_id, payload.message_id, str(payload.emoji)
        )
        if role_id is None:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return
        member = guild.get_member(payload.user_id)
        role = guild.get_role(role_id)
        if member is not None and role is not None:
            try:
                await member.remove_roles(role, reason="Reaction role removed")
            except discord.Forbidden:
                pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Roles(bot))
