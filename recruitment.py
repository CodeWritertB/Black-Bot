import asyncio
import json
import re
import time

import disnake as d

from cards import application_card
from server import REASON, button, log
from storage import CLAN, FIELDS, REVIEWERS, db, get_config, has, lookup, write

drafts = {}
locks = set()
ACTIVE = "'publishing','open','claimed','accepting','rejecting'"


def find(guild, number):
    return db.execute("SELECT * FROM applications WHERE guild=? AND id=?", (str(guild.id), number)).fetchone()


def controls(app):
    number, state = app["id"], app["status"]
    reviewer = f"<@{app['reviewer']}>"
    labels = dict(open="Ожидает рекрутёра", claimed=f"На рассмотрении у {reviewer}",
                  accepting=f"Принимается · {reviewer}", rejecting=f"Отклоняется · {reviewer}",
                  accepted=f"Принята · {reviewer}", rejected=f"Отклонена · {reviewer}", left="Кандидат покинул сервер", archived="Архив после сброса сервера")
    buttons = []
    if state == "open":
        buttons.append(button(f"review:claim:{number}", "Взять заявку", emoji="📥"))
    if state in ("claimed", "accepting"):
        buttons.append(button(f"review:accept:{number}", "Принять", d.ButtonStyle.success, emoji="✅"))
    if state in ("claimed", "rejecting"):
        buttons.append(button(f"review:reject:{number}", "Отклонить и кикнуть", d.ButtonStyle.danger, emoji="⛔"))
    if state == "claimed":
        buttons.append(button(f"review:release:{number}", "Вернуть в очередь", d.ButtonStyle.secondary, emoji="↩️"))
    return f"**Заявка №{number}** · <@{app['applicant']}>\n{labels[state]}", buttons


async def respond(inter, text, *, buttons=()):
    await inter.response.send_message(text, ephemeral=True, components=list(buttons))


def form_input(key, draft):
    label, maximum = FIELDS[key]
    return d.ui.Label(label, d.ui.TextInput(custom_id=key, max_length=maximum, value=draft.get(key),
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
    short = ("identity", "nick", "hours", "activity")
    if action in ("apply", "apply-edit"):
        selection = d.ui.StringSelect(custom_id="specialty", min_values=1, max_values=2, options=[
            d.SelectOption(label=label, value=code, default=code in previous_data.get("specialtyKeys", []))
            for label, code in [("PvP", "pvp"), ("PvE", "pve")]
        ])
        return await inter.response.send_modal(title="Заявка в Black Squad · 1 из 2", custom_id="bs:apply-part1",
                                               components=[*[form_input(k, previous_data) for k in short], d.ui.Label("Специализация", selection)])
    if action == "apply-part1":
        data = {field: inter.text_values[field].strip() for field in short}
        specialties = inter.values.get("specialty", [])
        match = re.fullmatch(r"(.*?)\s*[, ]\s*(\d{1,3})(?:\s+(?:лет|года|год))?", data["identity"])
        drafts[key] = data, time.monotonic() + 1800
        if not all(data.values()) or not re.fullmatch(r"[0-9]{1,7}", data["hours"]):
            return await respond(inter, "Заполни все поля. Часы укажи целым числом.", buttons=[button("apply-edit", "Исправить")])
        if not match or not 1 <= len(match[1].strip()) <= 27 or not 1 <= int(match[2]) <= 120:
            return await respond(inter, "Введи имя и возраст, например **Артём, 21**.", buttons=[button("apply-edit", "Исправить")])
        if not specialties or not set(specialties) <= {"pvp", "pve"}:
            return await respond(inter, "Выбери ПВП, ПВЕ или обе специализации.", buttons=[button("apply-edit", "Исправить")])
        data.update(name=match[1].strip(), specialtyKeys=specialties,
                    specialty=" / ".join({"pvp": "PvP", "pve": "PvE"}[item] for item in specialties))
        data["nick"] = " ".join(data["nick"].split())
        return await respond(inter, "Остались два поля.", buttons=[button("apply-next", "Продолжить"), button("apply-edit", "Изменить ответы")])
    if "name" not in previous_data:
        return await respond(inter, "Заполни первый шаг заявки заново.")
    if action == "apply-next":
        return await inter.response.send_modal(title="Заявка в Black Squad · 2 из 2", custom_id="bs:apply-submit",
                                               components=[form_input(key, previous_data) for key in ("previous", "expectations")])
    if action != "apply-submit":
        return
    data = {**previous_data, **{name: inter.text_values[name].strip() for name in ("previous", "expectations")}}
    if not data["previous"] or not data["expectations"]:
        return await respond(inter, "Заполни оба поля. Если кланов не было, напиши «Не состоял».", buttons=[button("apply-next", "Исправить")])
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
        png = await asyncio.to_thread(application_card, number, member.name, data)
        message = await channel.send(f"**Заявка №{number}** · {member.mention}", file=d.File(png, f"black-squad-{number}.png"))
    except Exception:
        write("DELETE FROM applications WHERE id=? AND status='publishing'", number)
        raise
    write("UPDATE applications SET status='open',message=? WHERE id=?", str(message.id), number)
    drafts.pop(key, None)
    await update_message(message, find(guild, number))
    await inter.edit_original_response(content=f"Заявка №{number} отправлена.")
    await log(guild, "Новая заявка", f"№{number} · {member.mention}", True)


async def update_message(message, app):
    text, buttons = controls(app)
    await message.edit(content=text, components=buttons)


async def review(inter, action, number):
    guild = inter.guild
    config = get_config(guild)
    if not has(inter.author, REVIEWERS, config) and inter.author.id != guild.owner_id:
        return await respond(inter, "Заявки рассматривает штаб.")
    app = find(guild, number)
    if not app or app["channel"] != str(inter.channel_id) or app["message"] != str(inter.message.id):
        return await respond(inter, "Кнопка относится к другой заявке.")
    await inter.response.defer(ephemeral=True)
    if number in locks:
        return await inter.edit_original_response(content="Заявку уже обрабатывают.")
    locks.add(number)
    try:
        app = find(guild, number)
        actor = str(inter.author.id)
        state = app["status"]
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
            await update_message(inter.message, find(guild, number))
        finally:
            locks.discard(number)


async def restore(guild):
    for app in db.execute(f"SELECT * FROM applications WHERE guild=? AND status IN ({ACTIVE})", (str(guild.id),)).fetchall():
        channel = guild.get_channel(int(app["channel"] or 0))
        if not channel:
            continue
        try:
            if app["status"] == "publishing":
                messages = [item async for item in channel.history(limit=50)
                                if item.author.id == guild.me.id and any(
                                    f.filename == f"black-squad-{app['id']}.png" for f in item.attachments)]
                message = messages[0] if messages else None
                if not message:
                    write("DELETE FROM applications WHERE id=? AND status='publishing'", app["id"])
                    continue
                write("UPDATE applications SET status='open',message=? WHERE id=?", str(message.id), app["id"])
            else:
                message = await channel.fetch_message(int(app["message"]))
            await update_message(message, find(guild, app["id"]))
        except d.NotFound:
            if app["status"] in ("open", "claimed"):
                write("UPDATE applications SET status='left' WHERE id=?", app["id"])
