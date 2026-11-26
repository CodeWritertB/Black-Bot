from dataclasses import dataclass

import disnake as d
from disnake.channel import _guild_channel_factory

import rules
from storage import db, get_config, save, write


@dataclass(frozen=True)
class ResetChannel:
    id: int
    name: str
    category: bool
    channel: d.abc.GuildChannel | None

    def deletable_by(self, member):
        if self.channel is None:
            return member.guild_permissions.administrator
        return self.channel.permissions_for(member).manage_channels


async def list_reset_channels(guild):
    try:
        channels = await guild.fetch_channels()
    except d.InvalidData:
        # The REST list can contain types that disnake cannot instantiate yet.
        state = guild._state
        result = []
        for item in await state.http.get_all_guild_channels(guild.id):
            factory, _ = _guild_channel_factory(item["type"])
            channel = factory(guild=guild, state=state, data=item) if factory else None
            result.append(ResetChannel(int(item["id"]), item.get("name") or item["id"],
                                       item["type"] == d.ChannelType.category.value, channel))
        return result
    return [ResetChannel(channel.id, channel.name, isinstance(channel, d.CategoryChannel), channel)
            for channel in channels]


async def replace(guild):
    from server import REQUIRED, REASON, install, log

    config = await rules.capture_legacy(guild)
    if guild.id == rules.LEGACY_GUILD and not config.get("rules_text"):
        raise ValueError("Правила из прежнего канала недоступны. Сначала запусти /rules_import и выбери канал с правилами.")
    missing = [name for name in REQUIRED if not getattr(guild.me.guild_permissions, name)]
    if missing:
        raise ValueError("Не хватает прав Black Bot: " + ", ".join(missing))

    channels = await list_reset_channels(guild)
    roles = [role for role in await guild.fetch_roles() if not role.is_default() and not role.managed]
    high = [role.name for role in roles if not role.is_assignable()]
    if high:
        raise ValueError("Подними управляемую роль Black Bot выше этих ролей: " + ", ".join(high))
    blocked = [channel.name for channel in channels if not channel.deletable_by(guild.me)]
    if blocked:
        raise ValueError("Black Bot не может управлять каналами: " + ", ".join(blocked))

    protected = {channel.id for channel in (guild.rules_channel, guild.public_updates_channel) if channel}
    if protected and "COMMUNITY" in guild.features:
        raise ValueError("Discord защищает системные каналы сообщества. Сначала отвяжи их в настройках сообщества.")

    state = config.get("reset_state")
    if state is None:
        config["reset_state"] = {
            "channels": [str(channel.id) for channel in channels],
            "roles": [str(role.id) for role in roles],
        }
        config["ready"] = False
        save(guild, config)
        state = config["reset_state"]

    # Try unsupported channel types first, before deleting channels that disnake knows.
    for channel in sorted(channels, key=lambda item: (item.channel is not None, item.category)):
        if str(channel.id) not in state["channels"]:
            continue
        try:
            if channel.channel is None:
                await guild._state.http.delete_channel(channel.id, reason=REASON)
            else:
                await channel.channel.delete(reason=REASON)
        except d.NotFound:
            pass
        except d.HTTPException as error:
            raise ValueError(f"Discord не удалил канал «{channel.name}» (ID {channel.id}): {error}") from error

    for role in reversed(roles):
        if str(role.id) not in state["roles"]:
            continue
        try:
            await role.delete(reason=REASON)
        except d.NotFound:
            pass

    if not state.get("cleared"):
        new = {key: config[key] for key in ("rules_text", "rules_source") if key in config}
        state["cleared"] = True
        new.update(channels={}, roles={}, categories={}, panels={}, reset_state=state)
        save(guild, new)
        write("DELETE FROM rooms WHERE guild=?", str(guild.id))
        write("""UPDATE applications SET status='archived' WHERE guild=?
                 AND status IN ('publishing','open','claimed','accepting','rejecting')""", str(guild.id))

    await install(guild)
    config = get_config(guild)
    config.pop("reset_state", None)
    save(guild, config)
    await log(guild, "✅ Структура пересоздана", "Каналы и обычные роли созданы заново.", True)
