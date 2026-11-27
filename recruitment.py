import asyncio
import json
import logging
import re
import time

import aiohttp
import disnake as d

from cards import application_card
from server import REASON, button, log, publish
from storage import CLAN, FIELDS, REVIEWERS, db, get_config, has, lookup, write

drafts = {}
locks = set()
panel_lock = asyncio.Lock()
ACTIVE = "'publishing','open','claimed','accepting','rejecting'"


def find(guild, number):
    return db.execute("SELECT * FROM applications WHERE guild=? AND id=?", (str(guild.id), number)).fetchone()


def controls(app):
    number, state = app["id"], app["status"]
    reviewer = f"<@{app['reviewer']}>"
    labels = dict(open="⏳ Ожидает рекрутёра", claimed=f"🔎 На рассмотрении у {reviewer}",
                  accepting=f"⏳ Принимается · {reviewer}", rejecting=f"⏳ Отклоняется · {reviewer}",
                  accepted=f"✅ Принята · {reviewer}", rejected=f"⛔ Отклонена · {reviewer}", left="🚪 Кандидат покинул сервер", archived="📦 Архив после сброса сервера")
    buttons = []
    if state == "open":
        buttons.append(button(f"review:claim:{number}", "Взять заявку", emoji="📥"))
    if state in ("claimed", "accepting"):
        buttons.append(button(f"review:accept:{number}", "Принять", d.ButtonStyle.success, emoji="✅"))
    if state in ("claimed", "rejecting"):
        buttons.append(button(f"review:reject:{number}", "Отклонить и кикнуть", d.ButtonStyle.danger, emoji="⛔"))
    if state in ("open", "claimed"):
        buttons.append(button(f"review:voice:{number}", "Позвать в Voice", emoji="🎙️"))
    if state == "claimed":
        buttons.append(button(f"review:release:{number}", "Вернуть в очередь", d.ButtonStyle.secondary, emoji="↩️"))
    return f"**📨 Заявка №{number}** · <@{app['applicant']}>\n{labels[state]}", buttons


async def respond(inter, text, *, buttons=()):
    await inter.response.send_message(text, ephemeral=True, components=list(buttons))


def form_input(key, draft):
    label, maximum, emoji = FIELDS[key]
    return d.ui.Label(f"{emoji} {label}", d.ui.TextInput(custom_id=key, max_length=maximum, value=draft.get(key),
                                           style=d.TextInputStyle.paragraph if maximum > 100 else d.TextInputStyle.short))


def roles_after(member, remove, add, config):
    exclude = {member.guild.id, *(int(config["roles"].get(key) or 0) for key in remove)}
    roles = {role.id: role for role in member.roles if role.id not in exclude}
    for key in add:
        role = lookup(member.guild, key, "roles", config)
        if not role or not role.is_assignable():
            raise ValueError(f"Роль {key} отсутствует или выше Black Bot.")
        roles[role.id] = role
    return list(roles.values())


async def onboarding(inter, action):
    guild, member = inter.guild, inter.author
    config = get_config(guild)
    if not config.get("ready"):
        return await respond(inter, "Вход ещё настраивается.")
    section = "verify" if action.startswith("verify") else "apply"
    if str(inter.channel_id) != str(config["channels"].get(section)):
        return await respond(inter, "Открой действующий канал набора.")
    if section == "verify":
        if has(member, ("guest", *CLAN), config):
            return await respond(inter, "Ты уже прошёл верификацию.")
        if action == "verify":
            field = d.ui.TextInput(label="Ник | Имя", custom_id="nickname", max_length=32, placeholder="CodeWriterB | Борис")
            return await inter.response.send_modal(title="Изменить никнейм", custom_id="bs:verify-submit", components=[field])
        parts = [part.strip() for part in inter.text_values["nickname"].split("|")]
        if len(parts) != 2 or not all(parts) or len(" | ".join(parts)) > 32:
            return await respond(inter, "Введи **Ник | Имя**, например **CodeWriterB | Борис**.")
        nick = " | ".join(parts)
        await inter.response.defer(ephemeral=True)
        await member.edit(nick=nick, roles=roles_after(member, ("unverified",), ("guest",), config), reason=REASON)
        await inter.edit_original_response(content="Верификация пройдена. Теперь доступны правила, описание клана и заявка.")
        return await log(guild, "Верификация", member.mention)
    if not has(member, ("guest",), config) or has(member, CLAN, config):
        return await respond(inter, "Подача доступна верифицированным кандидатам.")
    key = str(guild.id), str(member.id)
    active = db.execute(f"SELECT id FROM applications WHERE guild=? AND applicant=? AND status IN ({ACTIVE})", key).fetchone()
    if active:
        return await respond(inter, f"Заявка №{active['id']} уже на рассмотрении.")
    if key in drafts and drafts[key][1] < time.monotonic():
        drafts.pop(key)
    previous_data = drafts.get(key, ({}, 0))[0]
    first_fields = ("name", "age", "nick", "hours", "activity")
    final_fields = ("previous", "expectations")
    if action in ("apply", "apply-edit"):
        return await inter.response.send_modal(title="📝 Заявка в Black Squad · 1 из 2", custom_id="bs:apply-part1",
                                               components=[form_input(k, previous_data) for k in first_fields])
    if action == "apply-part1":
        if not all(field in inter.text_values for field in first_fields):
            return await respond(inter, "🔄 Форма обновилась. Открой заявку заново.")
        data = {**previous_data, **{field: inter.text_values[field].strip() for field in first_fields}}
        data.pop("first_complete", None)
        drafts[key] = data, time.monotonic() + 1800
        edit = [button("apply-edit", "Исправить", emoji="✏️")]
        if not all(data[field] for field in first_fields):
            return await respond(inter, "⚠️ Заполни все поля первого шага.", buttons=edit)
        if "|" in data["name"] or "|" in data["nick"] or len(data["name"]) > 27:
            return await respond(inter, "⚠️ Укажи имя до 27 символов. Убери знак | из имени и игрового ника.", buttons=edit)
        if not data["age"].isascii() or not data["age"].isdecimal() or not 1 <= int(data["age"]) <= 120:
            return await respond(inter, "🎂 Укажи возраст целым числом от 1 до 120.", buttons=edit)
        if not re.fullmatch(r"[0-9]{1,7}", data["hours"]):
            return await respond(inter, "⏳ Укажи количество часов целым числом.", buttons=edit)
        data["name"] = " ".join(data["name"].split())
        data["nick"] = " ".join(data["nick"].split())
        data["age"] = str(int(data["age"]))
        data["first_complete"] = True
        return await respond(inter, "✅ Первый шаг готов. Выбери специализацию и ответь на два вопроса.", buttons=[
            button("apply-next", "Продолжить", emoji="➡️"), button("apply-edit", "Изменить ответы", emoji="✏️"),
        ])
    if not previous_data.get("first_complete"):
        return await respond(inter, "🔄 Заполни первый шаг заявки заново.")
    if action == "apply-next":
        selection = d.ui.StringSelect(custom_id="specialty", min_values=1, max_values=2, options=[
            d.SelectOption(label=label, value=code, emoji=emoji, default=code in previous_data.get("specialtyKeys", []))
            for label, code, emoji in [("PvP", "pvp", "⚔️"), ("PvE", "pve", "🌾")]
        ])
        return await inter.response.send_modal(title="📝 Заявка в Black Squad · 2 из 2", custom_id="bs:apply-submit",
                                               components=[d.ui.Label("🎯 Специализация", selection),
                                                           *[form_input(k, previous_data) for k in final_fields]])
    if action != "apply-submit":
        return
    data = {**previous_data, **{name: inter.text_values[name].strip() for name in final_fields}}
    specialties = inter.values.get("specialty", [])
    if not specialties or not set(specialties) <= {"pvp", "pve"}:
        return await respond(inter, "🎯 Выбери PvP, PvE или обе специализации.",
                             buttons=[button("apply-next", "Исправить", emoji="✏️")])
    data.update(specialtyKeys=specialties,
                specialty=" / ".join({"pvp": "PvP", "pve": "PvE"}[item] for item in specialties))
    if not all(data[field] for field in final_fields):
        drafts[key] = data, time.monotonic() + 1800
        return await respond(inter, "⚠️ Заполни оба поля. Если кланов не было, напиши «Не состоял».",
                             buttons=[button("apply-next", "Исправить", emoji="✏️")])
    data.pop("first_complete", None)
    channel = lookup(guild, "reviews", "channels", config)
    if not channel:
        return await respond(inter, "Канал рассмотрения не настроен.")
    await inter.response.defer(ephemeral=True)
    entry = write("INSERT OR IGNORE INTO applications(guild,applicant,data,channel) VALUES (?,?,?,?)",
                  *key, json.dumps(data, ensure_ascii=False), str(channel.id))
    if not entry.rowcount:
        return await inter.edit_original_response(content="У тебя уже есть активная заявка.")
    number = entry.lastrowid
    try:
        png = await profile_card(number, member, data)
        filename = f"black-squad-{number}-profile.png"
        card = application_components(dict(id=number, applicant=str(member.id), status="open", reviewer=None),
                                      filename, interactive=False)
        message = await channel.send(components=[card], file=d.File(png, filename))
    except Exception:
        write("DELETE FROM applications WHERE id=? AND status='publishing'", number)
        raise
    write("UPDATE applications SET status='open',message=? WHERE id=?", str(message.id), number)
    drafts.pop(key, None)
    await update_message(message, find(guild, number))
    async with panel_lock:
        try:
            await publish(guild, get_config(guild), "reviews", channel, repost=True)
        except d.HTTPException:
            logging.exception("Не удалось переместить панель набора в конец канала")
    await inter.edit_original_response(content=f"✅ Заявка №{number} отправлена.")
    await log(guild, "Новая заявка", f"№{number} · {member.mention}", True)
    await notify_reviewers(guild, member, number, message, config)


async def notify_reviewers(guild, candidate, number, message, config):
    text = (f"📨 **Новая заявка в Black Squad №{number}**\n"
            f"Кандидат: {d.utils.escape_markdown(candidate.display_name)}\n"
            f"[Открыть заявку в Discord]({message.jump_url})")

    async def send(member):
        try:
            await member.send(text, allowed_mentions=d.AllowedMentions.none())
        except d.Forbidden:
            pass
        except d.HTTPException:
            logging.exception("Не удалось отправить уведомление о заявке %s участнику %s", number, member.id)

    await asyncio.gather(*(send(member) for member in guild.members if not member.bot
                           and member.id != candidate.id
                           and (member.id == guild.owner_id or has(member, REVIEWERS, config))))


async def profile_card(number, member, data):
    try:
        avatar = await asyncio.wait_for(member.display_avatar.replace(size=128, format="png").read(), timeout=4)
    except (d.HTTPException, aiohttp.ClientError, OSError, TimeoutError):
        avatar = None
    return await asyncio.to_thread(application_card, number, member.name, data, member.display_name, avatar)


def application_components(app, filename=None, *, interactive=True):
    text, buttons = controls(app)
    colors = {"open": 0xA855F7, "claimed": 0xFACC15, "accepting": 0xFACC15, "rejecting": 0xFACC15,
              "accepted": 0x22C55E, "rejected": 0xEF4444, "left": 0x64748B, "archived": 0x64748B}
    children = [d.ui.TextDisplay(text)]
    if filename:
        children.append(d.ui.MediaGallery(d.MediaGalleryItem(f"attachment://{filename}")))
    if interactive and buttons:
        children.append(d.ui.ActionRow(*buttons))
    return d.ui.Container(*children, accent_colour=d.Color(colors[app["status"]]))


def card_layout(component):
    return (component.accent_colour,
            [child.content for child in component.children if child.type == d.ComponentType.text_display],
            [len(child.items) for child in component.children if child.type == d.ComponentType.media_gallery],
            [(button.custom_id, button.label) for child in component.children if child.type == d.ComponentType.action_row
             for button in child.children])


async def update_message(message, app, member=None):
    filename = f"black-squad-{app['id']}-profile.png"
    images = [item for item in message.attachments if item.filename.lower().endswith(".png")]
    attachment = next((item for item in images if item.filename == filename), images[0] if images else None)
    keep = [item for item in message.attachments if item not in images]

    regenerate = attachment is None or (member is not None and attachment.filename != filename)
    image_name = filename if regenerate else attachment.filename if attachment else None
    card = application_components(app, image_name)
    if (not regenerate and len(images) <= 1 and message.flags.is_components_v2 and len(message.components) == 1
            and isinstance(message.components[0], d.Container)
            and card_layout(message.components[0]) == card_layout(card)):
        return
    payload = {"components": [card], "attachments": keep}
    if regenerate:
        data = json.loads(app["data"])
        if member is None:
            png = await asyncio.to_thread(application_card, app["id"], "", data, data.get("nick"))
        else:
            png = await profile_card(app["id"], member, data)
        payload["file"] = d.File(png, filename)
    elif attachment:
        payload["file"] = await attachment.to_file(use_cached=True)
    if not message.flags.is_components_v2 and (message.content or message.embeds):
        await message.edit(content=None, embeds=[])
    await message.edit(**payload)


async def invite_to_interview(inter, app, config, key):
    guild = inter.guild
    channel = lookup(guild, key, "channels", config)
    if not isinstance(channel, d.VoiceChannel):
        return await inter.edit_original_response(content="Голосовой канал «Собеседование» ещё не настроен.")
    try:
        candidate = await guild.fetch_member(int(app["applicant"]))
    except d.NotFound:
        return await inter.edit_original_response(content="Кандидат уже покинул сервер.")
    if not channel.permissions_for(candidate).connect:
        return await inter.edit_original_response(content="У кандидата нет доступа к «Собеседованию».")
    url = f"https://discord.com/channels/{guild.id}/{channel.id}"
    try:
        await candidate.send(f"🎙️ Black Squad ждёт тебя на собеседовании по заявке №{app['id']}.\n"
                             f"[Зайти в {channel.name}]({url})",
                             allowed_mentions=d.AllowedMentions.none())
    except d.Forbidden:
        return await inter.edit_original_response(content="Не удалось отправить приглашение: у кандидата закрыты ЛС.")
    await inter.edit_original_response(content=f"🎙️ Отправил {candidate.mention} ссылку на **{channel.name}**.")
    await log(guild, "Приглашение на собеседование",
              f"№{app['id']} · {inter.author.mention} → {candidate.mention}", True)


async def review(inter, action, number):
    guild = inter.guild
    config = get_config(guild)
    if not has(inter.author, REVIEWERS, config) and inter.author.id != guild.owner_id:
        return await respond(inter, "Заявки рассматривает штаб.")
    app = find(guild, number)
    rooms = {"voice-1": "interview", **{f"voice-{n}": f"interview{n}" for n in range(2, 5)}}
    proxy = bool(getattr(getattr(inter.message, "flags", None), "ephemeral", False))
    if not app or app["channel"] != str(inter.channel_id) or (
            not proxy and action != "voice-back" and action not in rooms and app["message"] != str(inter.message.id)):
        return await respond(inter, "Кнопка относится к другой заявке.")
    if action == "voice-back":
        url = f"https://discord.com/channels/{guild.id}/{app['channel']}/{app['message']}"
        text, buttons = controls(app)
        return await inter.response.edit_message(content=f"{text}\n[Открыть карточку заявки]({url})", components=buttons)
    await inter.response.defer(ephemeral=True)
    if number in locks:
        return await inter.edit_original_response(content="Заявку уже обрабатывают.")
    locks.add(number)
    try:
        app = find(guild, number)
        actor = str(inter.author.id)
        state = app["status"]
        if action == "voice" or action in rooms:
            if state != "open" and (state != "claimed" or app["reviewer"] != actor):
                return await inter.edit_original_response(content="Позвать можно из очереди или из заявки, которую ты взял.")
            if action == "voice":
                buttons = [button(f"review:{key}:{number}", f"Собеседование {i}", emoji="🎙️")
                           for i, key in enumerate(rooms, 1)]
                buttons.append(button(f"review:voice-back:{number}", "Назад", d.ButtonStyle.secondary, emoji="↩️"))
                return await inter.edit_original_response(content="Выбери войс для собеседования.", components=buttons)
            return await invite_to_interview(inter, app, config, rooms[action])
        if action == "claim":
            changed = write("UPDATE applications SET status='claimed',reviewer=? WHERE id=? AND status='open'", actor, number)
            if not changed.rowcount:
                return await inter.edit_original_response(content="Эту заявку уже взяли или закрыли.")
        elif app["reviewer"] != actor:
            return await inter.edit_original_response(content="Решение принимает тот, кто взял заявку.")
        elif action == "release" and state == "claimed":
            write("UPDATE applications SET status='open',reviewer=NULL WHERE id=?", number)
        elif action in ("accept", "reject"):
            accepting = action == "accept"
            processing = "accepting" if accepting else "rejecting"
            if state not in ("claimed", processing):
                return await inter.edit_original_response(content="По заявке уже есть решение.")
            write("UPDATE applications SET status=? WHERE id=?", processing, number)
            try:
                candidate = await guild.fetch_member(int(app["applicant"]))
            except d.NotFound:
                candidate = None
            if accepting and candidate:
                data = json.loads(app["data"])
                nick = data["nick"][:29 - len(data["name"])].rstrip()
                await candidate.edit(nick=f"{nick} | {data['name']}",
                                     roles=roles_after(candidate, ("guest", "unverified", "pvp", "pve"),
                                                       ("trial", *data["specialtyKeys"]), config),
                                     reason=f"Принята заявка №{number}")
                write("INSERT INTO activity(guild,user,last) VALUES (?,?,?) ON CONFLICT(guild,user) DO UPDATE SET last=excluded.last",
                      str(guild.id), str(candidate.id), int(time.time() * 1000))
            if not accepting and candidate:
                await candidate.kick(reason=f"Отклонена заявка Black Squad №{number}")
            result = ("accepted" if candidate else "left") if accepting else "rejected"
            write("UPDATE applications SET status=? WHERE id=?", result, number)
            await log(guild, "Решение по заявке", controls(find(guild, number))[0], True)
        else:
            return await inter.edit_original_response(content="Действие недоступно.")
        await inter.edit_original_response(content=controls(find(guild, number))[0])
    finally:
        try:
            if action != "voice" and action not in rooms:
                message = await inter.channel.fetch_message(int(app["message"])) if proxy else inter.message
                await update_message(message, find(guild, number))
        finally:
            locks.discard(number)


async def restore(guild, fetch_user=None):
    processed = missing = failed = 0
    for app in db.execute("SELECT * FROM applications WHERE guild=? AND (message IS NOT NULL OR status=?)",
                          (str(guild.id), "publishing")).fetchall():
        channel = guild.get_channel(int(app["channel"] or 0))
        if not channel:
            missing += 1
            continue
        try:
            if app["status"] == "publishing":
                messages = [item async for item in channel.history(limit=50)
                                if item.author.id == guild.me.id and any(
                                    f.filename in (f"black-squad-{app['id']}.png", f"black-squad-{app['id']}-profile.png") for f in item.attachments)]
                message = messages[0] if messages else None
                if not message:
                    write("DELETE FROM applications WHERE id=? AND status='publishing'", app["id"])
                    continue
                write("UPDATE applications SET status='open',message=? WHERE id=?", str(message.id), app["id"])
            else:
                message = await channel.fetch_message(int(app["message"]))
            candidate = None
            if not any(item.filename == f"black-squad-{app['id']}-profile.png" for item in message.attachments):
                user_id = int(app["applicant"])
                candidate = guild.get_member(user_id)
                if candidate is None:
                    try:
                        candidate = await guild.fetch_member(user_id)
                    except d.NotFound:
                        if fetch_user:
                            try:
                                candidate = await fetch_user(user_id)
                            except d.NotFound:
                                pass
            await update_message(message, find(guild, app["id"]), candidate)
            processed += 1
        except d.NotFound:
            missing += 1
            if app["status"] in ("open", "claimed"):
                write("UPDATE applications SET status='left' WHERE id=?", app["id"])
        except (d.HTTPException, aiohttp.ClientError, ValueError, TypeError, KeyError):
            failed += 1
            logging.exception("Не удалось обновить карточку заявки %s", app["id"])
    logging.info("Карточки заявок: проверено %d, недоступно %d, ошибок %d", processed, missing, failed)
    return processed, missing, failed
