"""bucket command permissions (review F7).

bucketadd had no permission check, so any member could push content into any
bucket, which bucket_pick then replays verbatim. Same manage_messages tier as
tagset.
"""

from discord.ext import commands

from bot.modules.bucket import Bucket


def test_bucket_add_requires_a_permission_check():
    assert Bucket.bucket_add.checks, "bucketadd must not be writable by every member"


def test_picking_and_listing_stay_open_to_everyone():
    assert not Bucket.bucket_pick.checks
    assert not Bucket.bucket_list.checks


def test_bucket_add_check_is_the_manage_messages_predicate():
    probe = commands.has_permissions(manage_messages=True)(lambda: None)
    assert [c.__qualname__ for c in Bucket.bucket_add.checks] == [
        c.__qualname__ for c in probe.__commands_checks__
    ]
