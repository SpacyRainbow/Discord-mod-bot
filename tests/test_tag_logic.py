"""tag command permissions (review F7).

tagset/tagdelete carried no permission check at all, so any member could
overwrite or delete any tag in the guild - silently replacing a staff-authored
`!tag rules` with anything. manage_messages is the "trusted with content" tier
this bot already uses for automod/antispam exemptions, and a lower bar than the
manage_guild required for config changes.
"""

from discord.ext import commands

from bot.modules.tag import Tag


def test_tag_set_requires_a_permission_check():
    assert Tag.tag_set.checks, "tagset must not be writable by every member"


def test_tag_delete_requires_a_permission_check():
    assert Tag.tag_delete.checks, "tagdelete must not be usable by every member"


def test_reading_tags_stays_open_to_everyone():
    # Reads are deliberately unrestricted - only the writes were the problem.
    assert not Tag.tag.checks
    assert not Tag.tag_list.checks


def test_tag_write_checks_are_the_manage_messages_predicate():
    """has_permissions attaches a predicate carrying the required perms; assert
    on that rather than simulating a full invocation."""
    for command in (Tag.tag_set, Tag.tag_delete):
        decorated = commands.has_permissions(manage_messages=True)
        probe = decorated(lambda: None)
        assert [c.__qualname__ for c in command.checks] == [
            c.__qualname__ for c in probe.__commands_checks__
        ]
