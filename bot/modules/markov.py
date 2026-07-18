"""
markov - generates a sentence by chaining words from recent channel history.

This builds the chain on-demand from the last N messages in the channel
(via channel.history()) rather than maintaining a growing corpus table.
Simpler to reason about and self-hosted-friendly - no separate ingestion
pipeline - at the cost of only "remembering" recent history per invocation.
If you want it to have a longer memory, the natural upgrade is a
`markov_corpus` table fed by an on_message listener.
"""

from __future__ import annotations

import random

from discord.ext import commands

HISTORY_LIMIT = 500
DEFAULT_LENGTH = 20


def build_chain(messages: list[str]) -> dict[str, list[str]]:
    chain: dict[str, list[str]] = {}
    for content in messages:
        words = content.split()
        for i in range(len(words) - 1):
            chain.setdefault(words[i], []).append(words[i + 1])
    return chain


def generate(chain: dict[str, list[str]], length: int = DEFAULT_LENGTH) -> str:
    if not chain:
        return ""
    word = random.choice(list(chain.keys()))
    output = [word]
    for _ in range(length - 1):
        next_words = chain.get(word)
        if not next_words:
            break
        word = random.choice(next_words)
        output.append(word)
    return " ".join(output)


class Markov(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="markov")
    @commands.cooldown(1, 10, commands.BucketType.channel)
    async def markov(self, ctx: commands.Context):
        messages = [
            m.content
            async for m in ctx.channel.history(limit=HISTORY_LIMIT)
            if m.content and not m.author.bot
        ]
        chain = build_chain(messages)
        result = generate(chain)
        await ctx.send(result or "Not enough channel history to work with yet.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Markov(bot))
