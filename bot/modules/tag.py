from __future__ import annotations

from discord.ext import commands


class Tag(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.hybrid_command(name="tag")
    async def tag(self, ctx: commands.Context, name: str):
        content = await self.bot.stores.tags.get(ctx.guild.id, name)
        if content is None:
            await ctx.send(f"No tag called `{name}`.")
            return
        await ctx.send(content)

    @commands.hybrid_command(name="tagset")
    async def tag_set(self, ctx: commands.Context, name: str, *, content: str):
        await self.bot.stores.tags.set(ctx.guild.id, name, content, ctx.author.id)
        await ctx.send(f"Tag `{name}` saved. Use `!tag {name}` to recall it.")

    @commands.hybrid_command(name="tagdelete")
    async def tag_delete(self, ctx: commands.Context, name: str):
        await self.bot.stores.tags.delete(ctx.guild.id, name)
        await ctx.send(f"Deleted tag `{name}`.")

    @commands.hybrid_command(name="tags")
    async def tag_list(self, ctx: commands.Context):
        names = await self.bot.stores.tags.list_names(ctx.guild.id)
        if not names:
            await ctx.send("No tags saved yet.")
            return
        await ctx.send(f"Tags: {', '.join(names)}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Tag(bot))
