from __future__ import annotations

import logging
import os
import sys

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

    bot = ModBot()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
