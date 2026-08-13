def member_is_admin(member) -> bool:
    """Return whether a guild member currently has administrator permission."""
    permissions = getattr(member, "guild_permissions", None)
    return bool(getattr(permissions, "administrator", False))


def _can_view(channel, member) -> bool:
    try:
        return bool(channel.permissions_for(member).view_channel)
    except (AttributeError, TypeError, ValueError):
        return False


def select_most_visible_channel(guild):
    """Select the self-visible text channel visible to most cached members.

    Returns ``(channel, visible_count, cached_member_count)``. Ties are resolved
    by the channel's UI position and then its ID so repeated scans are stable.
    """
    self_member = getattr(guild, "me", None)
    if self_member is None:
        return None

    members = list(getattr(guild, "members", ()) or ())
    best = None
    best_score = None
    for channel in getattr(guild, "text_channels", ()) or ():
        if not _can_view(channel, self_member):
            continue

        visible_count = sum(_can_view(channel, member) for member in members)
        position = getattr(channel, "position", 0)
        channel_id = getattr(channel, "id", 0)
        score = (visible_count, -position, -channel_id)
        if best_score is None or score > best_score:
            best = channel
            best_score = score

    if best is None:
        return None
    return best, best_score[0], len(members)
