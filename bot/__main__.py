from __future__ import annotations

import logging
import os
import sys

import discord

from dotenv import load_dotenv

from .core import ModBot


def main() -> None:
    load_dotenv()

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        stream=sys.stdout,
    )

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logging.getLogger("bot").error(
            "DISCORD_TOKEN is not set. Copy .env.example to .env and fill it in."
        )
        sys.exit(1)

    try:
        bot = ModBot(presences=True)
        bot.run(token, log_handler=None)
    except discord.PrivilegedIntentsRequired:
        # Presence Intent is not ticked in the developer portal. Start again
        # without it rather than crash-looping the whole bot behind
        # `restart: unless-stopped` - moderation, music and tickets do not
        # depend on presence, and only the member-profile lookup loses a field.
        logging.getLogger("bot").error(
            "Presence Intent is not enabled for this application, so member "
            "status will not be available. Enable it at "
            "https://discord.com/developers/applications -> your app -> Bot -> "
            "Privileged Gateway Intents -> Presence Intent, then restart. "
            "Starting without it."
        )
        bot = ModBot(presences=False)
        bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
