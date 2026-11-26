import asyncio
from collections import defaultdict

import aiohttp
import disnake as d

from cards import room_card
from recruitment import respond
from server import REASON, button, log
from storage import CLAN, db, get_config, has, lookup, write

creating = set()
room_locks = defaultdict(asyncio.Lock)


def find(channel):
    if not channel:
        return None
    return db.execute("SELECT * FROM rooms WHERE guild=? AND id=?", (str(channel.guild.id), str(channel.id))).fetchone()


def room_members(channel):
    return [member for member in channel.members if not member.bot]


def public_room_counts(guild):
    config = get_config(guild)
    member_role = lookup(guild, "member", "roles", config)
    if not config.get("ready") or member_role is None:
        return 0, 0
    rooms_count = people_count = 0
    rows = db.execute("SELECT id FROM rooms WHERE guild=? AND hidden=0", (str(guild.id),))
    for row in rows:
        channel = guild.get_channel(int(row["id"]))
        if not isinstance(channel, d.VoiceChannel) or not channel.permissions_for(member_role).view_channel:
            continue
        rooms_count += 1
        people_count += len(room_members(channel))
    return rooms_count, people_count


def presence_text(counts):
    def amount(number, one, few, many):
        if 11 <= number % 100 <= 14:
            word = many
        else:
            last = number % 10
            word = one if last == 1 else few if 2 <= last <= 4 else many
        return f"{number} {word}"

    room_count, people_count = counts
    return (f"🎧 {amount(room_count, 'комната', 'комнаты', 'комнат')} · "
            f"👥 {amount(people_count, 'человек', 'человека', 'человек')}")


def prefs(guild, owner):
    row = db.execute("SELECT * FROM room_preferences WHERE guild=? AND owner=?",
                     (str(guild.id), str(owner.id))).fetchone()
    return dict(row) if row else dict(name=None, user_limit=0, locked=0, hidden=0)


def save_prefs(guild, owner, **changes):
    settings = prefs(guild, owner)
    settings.update(changes)
    write("""INSERT INTO room_preferences VALUES (?,?,?,?,?,?)
          ON CONFLICT(guild,owner) DO UPDATE SET name=excluded.name,user_limit=excluded.user_limit,
          locked=excluded.locked,hidden=excluded.hidden""",
          str(guild.id), str(owner.id), *(settings[key] for key in ("name", "user_limit", "locked", "hidden")))


def room_permissions(guild, owner, locked, hidden):
    result = {
        guild.default_role: d.PermissionOverwrite(view_channel=False, connect=False),
        guild.me: d.PermissionOverwrite(view_channel=True, connect=True, send_messages=True, read_message_history=True),
        owner: d.PermissionOverwrite(view_channel=True, connect=True),
    }
    config = get_config(guild)
    for key in CLAN:
        role = lookup(guild, key, "roles", config)
        if role:
            can_move = key in ("deputy", "coder")
            result[role] = d.PermissionOverwrite(view_channel=can_move or not hidden, connect=can_move or not locked, speak=True,
                                                use_voice_activation=True,
                                                send_messages=True, read_message_history=True)
    return result


async def avatar_bytes(asset):
    if asset is None:
        return None
    try:
        return await asyncio.wait_for(asset.replace(size=128, format="png").read(), timeout=4)
    except (d.HTTPException, aiohttp.ClientError, OSError, TimeoutError):
        return None


async def panel(channel, owner):
    room = find(channel)
    if not room:
        return
    server_icon, owner_avatar = await asyncio.gather(avatar_bytes(channel.guild.icon), avatar_bytes(owner.display_avatar))
    png = await asyncio.to_thread(room_card, channel.guild.name, channel, owner, room["locked"], room["hidden"],
                                  server_icon, owner_avatar)
    file = d.File(png, "room.png")
    embed = d.Embed(title="Управление временной комнатой", description=f"Владелец {owner.mention}", color=0xa98658)
    embed.set_image(url="attachment://room.png")
    buttons = [button("room:name", "Название", emoji="✏️"), button("room:limit", "Лимит", emoji="👥"),
               button("room:lock", "Вход", emoji="🔒"), button("room:hide", "Видимость", emoji="👁️"),
               button("room:transfer", "Передать комнату", emoji="🔁"),
               button("room:kick", "Кикнуть из комнаты", d.ButtonStyle.danger, emoji="🚪")]
    if room["panel"]:
        try:
            message = await channel.fetch_message(int(room["panel"]))
            await message.edit(embed=embed, attachments=[], file=file, components=buttons)
            return
        except d.NotFound:
            pass
    message = await channel.send(embed=embed, file=file, components=buttons)
    write("UPDATE rooms SET panel=? WHERE id=?", str(message.id), str(channel.id))


async def transfer(channel, old_owner_id, owner):
    overrides = channel.overwrites
    # Remove the previous owner even if Discord no longer resolves their member from cache.
    overrides = {target: overwrite for target, overwrite in overrides.items() if target.id != old_owner_id}
    overrides[owner] = d.PermissionOverwrite(view_channel=True, connect=True)
    await channel.edit(overwrites=overrides, reason=REASON)
    write("UPDATE rooms SET owner=? WHERE id=?", str(owner.id), str(channel.id))
    await panel(channel, owner)


async def reconcile(channel, *, refresh=False):
    if not find(channel):
        return
    async with room_locks[channel.id]:
        room = find(channel)
        if not room:
            return
        members = room_members(channel)
        if not members:
            try:
                await channel.delete(reason=REASON)
            except d.NotFound:
                pass
            write("DELETE FROM rooms WHERE id=?", str(channel.id))
            room_locks.pop(channel.id, None)
            return
        owner_id = int(room["owner"])
        owner = next((member for member in members if member.id == owner_id), None)
        if owner is None:
            await transfer(channel, owner_id, members[0])
        elif refresh:
            await panel(channel, owner)


async def restore_staff_access(channel):
    config = get_config(channel.guild)
    for key in ("deputy", "coder"):
        role = lookup(channel.guild, key, "roles", config)
        if role is None:
            continue
        overwrite = channel.overwrites_for(role)
        if overwrite.view_channel is False or overwrite.connect is False:
            overwrite.view_channel = True
            overwrite.connect = True
            await channel.set_permissions(role, overwrite=overwrite, reason=REASON)


async def restore_rooms(guild):
    for row in db.execute("SELECT id FROM rooms WHERE guild=?", (str(guild.id),)).fetchall():
        channel = guild.get_channel(int(row["id"]))
        if channel:
            await restore_staff_access(channel)
            await reconcile(channel, refresh=True)
        else:
            write("DELETE FROM rooms WHERE id=?", row["id"])


async def voice_change(member, before, after):
    if before.channel == after.channel:
        return
    await reconcile(before.channel)
    guild = member.guild
    config = get_config(guild)
    lobby = lookup(guild, "lobby", "channels", config)
    if not lobby or after.channel != lobby or member.bot or not has(member, CLAN, config) or member.id in creating:
        return
    creating.add(member.id)
    channel = None
    try:
        settings = prefs(guild, member)
        channel = await guild.create_voice_channel(
            settings["name"] or f"🎧 Комната {member.display_name}"[:100], category=lobby.category,
            user_limit=settings["user_limit"], overwrites=room_permissions(guild, member, settings["locked"], settings["hidden"]),
            reason=REASON,
        )
        write("INSERT INTO rooms(id,guild,owner,locked,hidden) VALUES (?,?,?,?,?)",
              str(channel.id), str(guild.id), str(member.id), settings["locked"], settings["hidden"])
        if not member.voice or member.voice.channel != lobby:
            return await reconcile(channel)
        await member.move_to(channel, reason=REASON)
        await panel(channel, member)
    except Exception:
        if channel and (not room_members(channel)) and (not member.voice or member.voice.channel != channel):
            await reconcile(channel)
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
    if action in ("transfer", "kick"):
        candidates = [m for m in room_members(channel) if m.id != inter.author.id]
        if not candidates:
            return await respond(inter, "В комнате пока нет другого участника.")
        custom_id = f"bs:room:{action}-to"
        if len(candidates) > 25:
            select = d.ui.UserSelect(custom_id=custom_id, placeholder="Участник комнаты")
        else:
            options = [d.SelectOption(label=m.display_name[:100], value=str(m.id)) for m in candidates]
            select = d.ui.StringSelect(custom_id=custom_id, options=options)
        prompt = "Кого отключить от комнаты?" if action == "kick" else "Кому передать комнату?"
        return await respond(inter, prompt, buttons=[select])
    await inter.response.defer(ephemeral=True)
    async with room_locks[channel.id]:
        room = find(channel)
        if not room or room["owner"] != str(inter.author.id):
            return await inter.edit_original_response(content="Комната закрыта или владелец изменился.")
        if action in ("transfer-to", "kick-to"):
            target = next((m for m in room_members(channel) if str(m.id) == inter.values[0]), None)
            if not target or target.id == inter.author.id:
                return await inter.edit_original_response(content="Этот участник уже вышел или владеет комнатой.")
            if action == "kick-to":
                if target.voice is None or target.voice.channel != channel:
                    return await inter.edit_original_response(content="Этот игрок уже вышел из комнаты.")
                await target.move_to(None, reason=f"Владелец {inter.author.id} отключил от временной комнаты")
                await log(inter.guild, "Игрок отключён от временной комнаты",
                          f"{inter.author.mention} отключил {target.mention} · {channel.mention}")
                return await inter.edit_original_response(content=f"🚪 {target.mention} отключён от комнаты.")
            await transfer(channel, inter.author.id, target)
            await log(inter.guild, "Владелец комнаты изменён", f"{inter.author.mention} → {target.mention} · {channel.mention}")
            return await inter.edit_original_response(content=f"Комната передана {target.mention}.")
        if action == "set-name":
            name = inter.text_values["value"].strip()
            if not name:
                return await inter.edit_original_response(content="Укажи название.")
            updated = await channel.edit(name=name, reason=REASON)
            save_prefs(inter.guild, inter.author, name=name)
        elif action == "set-limit":
            value = inter.text_values["value"].strip()
            if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= 99:
                return await inter.edit_original_response(content="Укажи число от 0 до 99.")
            updated = await channel.edit(user_limit=int(value), reason=REASON)
            save_prefs(inter.guild, inter.author, user_limit=int(value))
        elif action in ("lock", "hide"):
            locked = not room["locked"] if action == "lock" else bool(room["locked"])
            hidden = not room["hidden"] if action == "hide" else bool(room["hidden"])
            updated = await channel.edit(overwrites=room_permissions(inter.guild, inter.author, locked, hidden), reason=REASON)
            write("UPDATE rooms SET locked=?,hidden=? WHERE id=?", int(locked), int(hidden), room["id"])
            save_prefs(inter.guild, inter.author, locked=int(locked), hidden=int(hidden))
        else:
            return await inter.edit_original_response(content="Неизвестное действие.")
        await panel(updated or channel, inter.author)
        await inter.edit_original_response(content="Настройки комнаты обновлены.")
