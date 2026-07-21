from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from bot.modules.setup import (
    CONFIG_MANIFEST,
    STEP_RAID,
    STEPS,
    SetupView,
    _ResetButton,
    build_summary_lines,
    combined_status_color,
    format_status,
)
from bot.stores import Stores

GUILD = 111


def _make_cog(db):
    cog = MagicMock()
    cog.bot.stores = Stores(db)
    cog.moderation_cog = MagicMock(return_value=None)
    return cog


@pytest.mark.asyncio
async def test_every_wizard_step_rebuilds_items_and_embed_without_crashing(db):
    """Regression test: each step's branch in _rebuild_items/_build_embed is
    matched by string constant - a typo there silently falls through to no
    branch instead of raising, so this walks every step and checks each
    actually produces a titled embed rather than an empty one."""
    cog = _make_cog(db)
    guild = MagicMock(id=GUILD)
    view = SetupView(cog, guild, invoker_id=1)

    for index in range(len(STEPS)):
        view.step_index = index
        await view._rebuild_items()
        embed = await view._build_embed()
        assert embed.title
        assert embed.description or embed.fields


def test_build_summary_lines_shows_default_when_unset():
    lines = build_summary_lines({})
    assert any("`spam.max_messages`" in line and "5 (default)" in line for line in lines)


def test_build_summary_lines_shows_stored_value_and_default_when_set():
    lines = build_summary_lines({"spam.max_messages": "10"})
    matching = [line for line in lines if "`spam.max_messages`" in line]
    assert matching and "10" in matching[0] and "default: 5" in matching[0]


def test_build_summary_lines_renders_booleans_as_status_not_raw_true_false():
    lines = build_summary_lines({"automod.block_invites": "false"})
    matching = [line for line in lines if "`automod.block_invites`" in line]
    assert matching
    assert "Disabled" in matching[0]
    assert "true" not in matching[0].lower().split("default:")[0]  # current value isn't raw "false"


def test_build_summary_lines_covers_every_manifest_key():
    lines = build_summary_lines({})
    assert len(lines) == len(CONFIG_MANIFEST)


def test_format_status_enabled():
    assert "Enabled" in format_status(True)
    assert "\N{LARGE GREEN CIRCLE}" in format_status(True)


def test_format_status_disabled():
    assert "Disabled" in format_status(False)
    assert "\N{LARGE RED CIRCLE}" in format_status(False)


def test_combined_status_color_all_on_is_green():
    assert combined_status_color([True, True, True]) == discord.Color.green()


def test_combined_status_color_all_off_is_red():
    assert combined_status_color([False, False, False]) == discord.Color.red()


def test_combined_status_color_mixed_is_blurple():
    assert combined_status_color([True, False]) == discord.Color.blurple()


@pytest.mark.asyncio
async def test_raid_step_color_reflects_all_toggles_state(db):
    cog = _make_cog(db)
    guild = MagicMock(id=GUILD)
    view = SetupView(cog, guild, invoker_id=1)
    view.step_index = STEPS.index(STEP_RAID)

    embed = await view._build_embed()

    assert embed.color == discord.Color.red()  # nothing configured yet - all off


@pytest.mark.asyncio
async def test_reset_button_restores_literal_defaults(db):
    bot_stores = Stores(db)
    await bot_stores.config.set(GUILD, "raid.min_account_age_hours", "48")
    await bot_stores.config.set(GUILD, "raid.auto_lockdown", "true")

    cog = _make_cog(db)
    cog.bot.stores = bot_stores
    guild = MagicMock(id=GUILD)
    view = SetupView(cog, guild, invoker_id=1)
    view.step_index = STEPS.index(STEP_RAID)

    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    button = _ResetButton()
    view.add_item(button)
    await button.callback(interaction)

    assert await bot_stores.config.get(GUILD, "raid.min_account_age_hours") == "0"
    assert await bot_stores.config.get(GUILD, "raid.auto_lockdown") == "false"


@pytest.mark.asyncio
async def test_reset_button_deletes_unset_style_keys(db):
    from bot.modules.setup import STEP_STARBOARD

    bot_stores = Stores(db)
    await bot_stores.config.set(GUILD, "starboard.channel", "12345")
    await bot_stores.config.set(GUILD, "starboard.threshold", "9")

    cog = _make_cog(db)
    cog.bot.stores = bot_stores
    guild = MagicMock(id=GUILD)
    view = SetupView(cog, guild, invoker_id=1)
    view.step_index = STEPS.index(STEP_STARBOARD)

    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    button = _ResetButton()
    view.add_item(button)
    await button.callback(interaction)

    assert await bot_stores.config.get(GUILD, "starboard.channel") is None
    assert await bot_stores.config.get(GUILD, "starboard.threshold") == "3"


@pytest.mark.asyncio
async def test_reset_button_does_not_touch_other_steps_keys(db):
    bot_stores = Stores(db)
    await bot_stores.config.set(GUILD, "spam.max_messages", "99")
    await bot_stores.config.set(GUILD, "raid.min_account_age_hours", "48")

    cog = _make_cog(db)
    cog.bot.stores = bot_stores
    guild = MagicMock(id=GUILD)
    view = SetupView(cog, guild, invoker_id=1)
    view.step_index = STEPS.index(STEP_RAID)

    interaction = MagicMock()
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()

    button = _ResetButton()
    view.add_item(button)
    await button.callback(interaction)

    assert await bot_stores.config.get(GUILD, "spam.max_messages") == "99"  # untouched
