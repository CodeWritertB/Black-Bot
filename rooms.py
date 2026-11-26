import asyncio
from collections import defaultdict

import disnake as d

from recruitment import respond
from server import REASON, button
from storage import CLAN, db, get_config, has, lookup, write

creating = set()
room_locks = defaultdict(asyncio.Lock)


def find(channel):
    if not channel:
        return None
    return db.execute("SELECT * FROM rooms WHERE guild=? AND id=?", (str(channel.guild.id), str(channel.id))).fetchone()


async def clean(channel):
    if not find(channel) or channel.members:
        return
    try:
        await channel.delete(reason=REASON)
    except d.NotFound:
        pass
    write("DELETE FROM rooms WHERE id=?", str(channel.id))
    room_locks.pop(channel.id, None)


async def restore_rooms(guild):
    for row in db.execute("SELECT id FROM rooms WHERE guild=?", (str(guild.id),)).fetchall():
        channel = guild.get_channel(int(row["id"]))
        if channel:
            await clean(channel)
        else:
            write("DELETE FROM rooms WHERE id=?", row["id"])


async def voice_change(member, before, after):
    if before.channel == after.channel:
        return
    await clean(before.channel)
    guild = member.guild
    config = get_config(guild)
    lobby = lookup(guild, "lobby", "channels", config)
    if not lobby or after.channel != lobby or member.bot or not has(member, CLAN, config) or member.id in creating:
        return
    creating.add(member.id)
    channel = None
    try:
        overwrites = {
            guild.default_role: d.PermissionOverwrite(view_channel=False, connect=False),
            guild.me: d.PermissionOverwrite(view_channel=True, connect=True, send_messages=True, read_message_history=True),
            member: d.PermissionOverwrite(view_channel=True, connect=True),
        }
        for key in CLAN:
            role = lookup(guild, key, "roles", config)
            if role:
                overwrites[role] = d.PermissionOverwrite(view_channel=True, connect=True, speak=True,
                                                         send_messages=True, read_message_history=True)
        channel = await guild.create_voice_channel(f"Комната {member.display_name}"[:100], category=lobby.category,
                                                   overwrites=overwrites, reason=REASON)
        write("INSERT INTO rooms(id,guild,owner) VALUES (?,?,?)", str(channel.id), str(guild.id), str(member.id))
        if not member.voice or member.voice.channel != lobby:
            return await clean(channel)
        await member.move_to(channel, reason=REASON)
        await channel.send("**Управление временной комнатой**", components=[
            button("room:name", "Название"), button("room:limit", "Лимит"), button("room:lock", "Закрыть / открыть вход"),
        ])
    except Exception:
        await clean(channel)
        raise
    finally:
        creating.discard(member.id)


async def room_action(inter, action):
    channel = inter.channel
    room = find(channel)
    if not room or room["owner"] != str(inter.author.id):
        return await respond(inter, "Управление доступно владельцу комнаты.")
    if action in ("name", "limit"):
        field = d.ui.TextInput(label="Новое название" if action == "name" else "Лимит 0–99 (0 — без лимита)",
                               custom_id="value", max_length=100 if action == "name" else 2)
        return await inter.response.send_modal(title="Настройки комнаты", custom_id=f"bs:room:set-{action}", components=[field])
    await inter.response.defer(ephemeral=True)
    async with room_locks[channel.id]:
        room = find(channel)
        if not room:
            return await inter.edit_original_response(content="Комната закрыта.")
        if action == "set-name":
            name = inter.text_values["value"].strip()
            if not name:
                return await inter.edit_original_response(content="Укажи название.")
            await channel.edit(name=name, reason=REASON)
        elif action == "set-limit":
            value = inter.text_values["value"].strip()
            if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= 99:
                return await inter.edit_original_response(content="Укажи число от 0 до 99.")
            await channel.edit(user_limit=int(value), reason=REASON)
        elif action == "lock":
            existing = channel.overwrites
            config = get_config(inter.guild)
            for key in CLAN:
                role = lookup(inter.guild, key, "roles", config)
                if role in existing:
                    existing[role].connect = bool(room["locked"])
            await channel.edit(overwrites=existing, reason=REASON)
            write("UPDATE rooms SET locked=? WHERE id=?", int(not room["locked"]), room["id"])
            return await inter.edit_original_response(content="Вход открыт." if room["locked"] else "Вход закрыт.")
        await inter.edit_original_response(content="Настройки комнаты обновлены.")
