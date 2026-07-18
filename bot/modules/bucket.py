from __future__ import annotations

from discord.ext import commands


class Bucket(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="bucketadd")
    async def bucket_add(self, ctx: commands.Context, bucket_name: str, *, item: str):
        await self.bot.stores.buckets.add(ctx.guild.id, bucket_name, item)
        await ctx.send(f"Added to `{bucket_name}`.")

    @commands.command(name="bucket")
    async def bucket_pick(self, ctx: commands.Context, bucket_name: str):
        item = await self.bot.stores.buckets.random(ctx.guild.id, bucket_name)
        if item is None:
            await ctx.send(f"Bucket `{bucket_name}` is empty or doesn't exist yet.")
            return
        await ctx.send(item)

    @commands.command(name="buckets")
    async def bucket_list(self, ctx: commands.Context):
        names = await self.bot.stores.buckets.list_buckets(ctx.guild.id)
        if not names:
            await ctx.send("No buckets yet.")
            return
        await ctx.send(f"Buckets: {', '.join(names)}")


async def setup(bot: commands.Bot):
    await bot.add_cog(Bucket(bot))
