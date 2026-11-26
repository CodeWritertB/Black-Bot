import asyncio
import io
import logging
import os
import time

import disnake as d
from disnake.ext import commands, tasks

import activity
from admin_panel import AdminPanel, status_embed
import recruitment
import rooms
import rules
import server
import streaks
from recruitment import respond
from storage import LEVELS, REVIEWERS, VOICE_XP_PER_MINUTE, db, get_config, has, lookup, write

GUILD_ID = int(os.getenv("DISCORD_GUILD_ID") or 0)
def missing_intents(intents, flags):
    missing = []
    if intents.members and not (flags.gateway_guild_members or flags.gateway_guild_members_limited):
        missing.append("Server Members Intent")
    if intents.message_content and not (flags.gateway_message_content or flags.gateway_message_content_limited):
        missing.append("Message Content Intent")
    return missing


class StartupConfigurationError(Exception):
    pass


class BlackBot(commands.InteractionBot):
    async def login(self, token):
        await super().login(token)
        app = await self.application_info()
        logging.info("Токен принадлежит приложению %s (ID %s)", app.name, app.id)
        missing = missing_intents(self.intents, app.flags)
        if missing:
            raise StartupConfigurationError(
                "Discord сообщает, что выключены: " + ", ".join(missing)
                + f". Включи их на https://discord.com/developers/applications/{app.id}/bot"
                " и нажми Save Changes."
            )


sync_flags = commands.CommandSyncFlags.default()
sync_flags.sync_global_commands = False
sync_flags.sync_commands_debug = True
sync_flags.allow_command_deletion = True
bot = BlackBot(
    command_sync_flags=sync_flags,
    intents=d.Intents(guilds=True, members=True, messages=True, message_content=True, voice_states=True, moderation=True),
    test_guilds=[GUILD_ID] if GUILD_ID else None, allowed_mentions=d.AllowedMentions.none(),
)
SERVER_MANAGER = d.Permissions(manage_guild=True)
config_lock = asyncio.Lock()
last_streak_day = None
last_room_presence = None


def enabled(guild):
    return guild and guild.id == GUILD_ID


async def report(inter, heading, lines, name):
    content = "\n".join(lines)
    if not content:
        return await inter.edit_original_response(content=f"{heading} · замечаний нет.")
    if len(content) < 1800:
        return await inter.edit_original_response(content=f"**{heading}**\n{content}")
    await inter.edit_original_response(content=heading, file=d.File(io.BytesIO(content.encode()), f"{name}.txt"))


@bot.slash_command(description="Применить настройки Black Squad к существующим каналам и ролям", default_member_permissions=SERVER_MANAGER)
async def setup(inter):
    if not enabled(inter.guild) or not server.can_configure(inter.author):
        return await respond(inter, "Команда доступна администратору или Кодеру.")
    if config_lock.locked():
        return await respond(inter, "Настройка уже выполняется.")
    await inter.response.defer(ephemeral=True)
    async with config_lock:
        await rules.capture_legacy(inter.guild)
        await server.install(inter.guild)
        for member in inter.guild.members:
            activity.seed(member)
    await inter.edit_original_response(content="✅ Настройки Black Squad применены. Существующие каналы и роли сохранены.")


async def rules_import(inter, channel=None, file=None):
    if not enabled(inter.guild) or not server.can_configure(inter.author):
        return await respond(inter, "Команда доступна администратору или Кодеру.")
    if config_lock.locked():
        return await respond(inter, "Настройка уже выполняется.")
    await inter.response.defer(ephemeral=True)
    async with config_lock:
        target = lookup(inter.guild, "rules", "channels")
        if (channel is None) == (file is None):
            raise ValueError("Выбери канал с правилами или прикрепи текстовый файл.")
        if channel:
            if target and target.id == channel.id:
                raise ValueError("Выбери исходный канал с правилами.")
            config = await rules.capture(inter.guild, channel)
        else:
            if file.size > 256_000 or not file.filename.lower().endswith((".txt", ".md")):
                raise ValueError("Прикрепи файл .txt или .md размером до 256 КБ.")
            try:
                text = (await file.read()).decode("utf-8-sig")
            except UnicodeDecodeError:
                raise ValueError("Сохрани текст правил в UTF-8.") from None
            config = rules.store(inter.guild, file.filename, [text.strip()])
        if target:
            await server.publish(inter.guild, config, "rules", target)
    await inter.edit_original_response(content=f"📜 Правила сохранены. Страниц: {len(config['rules_text'])}.")


@bot.slash_command(description="Назначить каналы Black Squad через кнопки", default_member_permissions=SERVER_MANAGER)
async def admin(inter):
    await open_admin(inter)


async def open_admin(inter):
    if not enabled(inter.guild) or not server.can_configure(inter.author):
        return await respond(inter, "Настройка доступна администратору или Кодеру.")
    await inter.response.send_message(embed=status_embed(inter.guild),
                                      view=AdminPanel(inter.guild, config_lock), ephemeral=True)


async def server_audit(inter):
    if not enabled(inter.guild) or not server.can_configure(inter.author):
        return await respond(inter, "Команда доступна администратору или Кодеру.")
    await inter.response.defer(ephemeral=True)
    await report(inter, "Аудит Black Squad", await server.audit(inter.guild), "server_audit")


async def inactive(inter):
    if not enabled(inter.guild) or not inter.author.guild_permissions.administrator:
        return await respond(inter, "Команда доступна администрации Black Squad.")
    await inter.response.defer(ephemeral=True)
    candidates = await activity.inactive(inter.guild)
    await report(inter, "Неактив 30 дней", [f"{m.mention} · {m.id}" for m in candidates], "inactive")


async def can_read_stats(inter):
    if enabled(inter.guild) and (inter.author.guild_permissions.administrator or activity.enrolled(inter.author)):
        return True
    await respond(inter, "Статистика доступна участникам Black Squad.")
    return False


async def rank(inter, member: d.Member = None):
    if not await can_read_stats(inter):
        return
    member = member or inter.author
    total = activity.xp(member)
    level = activity.level(total)
    if level < len(LEVELS):
        remaining = LEVELS[level] - total
        minutes = (remaining + VOICE_XP_PER_MINUTE - 1) // VOICE_XP_PER_MINUTE
        hours, mins = divmod(minutes, 60)
        progress = f"До уровня {level + 1}: **{remaining} XP** (≈ {hours} ч {mins} мин в войсе)"
    else:
        progress = "🏆 Максимальный уровень"
    await respond(inter, f"**Уровень {level}** · {'⭐' * level or '0 звёзд'} · {member.mention}\n"
                         f"**{total} XP**\n{progress}")


async def streak(inter, member: d.Member = None):
    if not await can_read_stats(inter):
        return
    member = member or inter.author
    if not activity.enrolled(member):
        return await respond(inter, "Голосовые серии доступны участникам клана.")
    item = streaks.state(member)
    if item and item["hot"]:
        text = f"🔥 Дней подряд в голосовых: **{item['hot']}**"
    elif item and item["cold"]:
        text = f"❄️ Пропущено дней подряд: **{item['cold']}**"
    else:
        text = "Голосовая серия ещё не началась."
    await respond(inter, f"{member.mention}\n{text}\nДни считаются по Москве.")


async def activity_command(inter, member: d.Member = None):
    if not await can_read_stats(inter):
        return
    member = member or inter.author
    item = activity.get_activity(member)
    if not item:
        return await respond(inter, "Данные активности ещё не записаны.")
    await respond(inter, f"**Активность** · {member.mention}\nСообщений: {item['messages']}\n"
                         f"Минут в голосовых: {item['voice_ms'] // 60000}\nПоследняя активность: <t:{item['last'] // 1000}:R>")


async def top(inter):
    if not await can_read_stats(inter):
        return
    values = db.execute("SELECT user,total FROM xp WHERE guild=? ORDER BY total DESC,user", (str(inter.guild.id),))
    members = [item for item in values if inter.guild.get_member(int(item["user"]))][:10]
    lines = [f"{i}. <@{item['user']}> · {item['total']} XP" for i, item in enumerate(members, 1)]
    await respond(inter, "\n".join(lines) or "Таблица пока пуста.")


async def promote(inter, member):
    if not enabled(inter.guild) or (not has(inter.author, REVIEWERS) and inter.author.id != inter.guild.owner_id):
        return await respond(inter, "Перевод в состав доступен штабу.")
    if not has(member, ("trial",)):
        return await respond(inter, "Выбери пробника.")
    await inter.response.defer(ephemeral=True)
    await member.edit(roles=recruitment.roles_after(member, ("trial",), ("member",), get_config(inter.guild)), reason=server.REASON)
    await inter.edit_original_response(content=f"{member.mention} теперь в основном составе.")
    await server.log(inter.guild, "Перевод в основной состав", f"{inter.author.mention} · {member.mention}", True)


async def tools_action(inter, action):
    if action == "admin":
        return await open_admin(inter)
    if action in ("audit", "inactive"):
        handler = server_audit if action == "audit" else inactive
        return await handler(inter)
    if action.startswith("rules"):
        if not server.can_configure(inter.author):
            return await respond(inter, "Импорт правил доступен администратору или Кодеру.")
        if action == "rules":
            return await respond(inter, "📜 Откуда перенести правила?", buttons=[
                server.button("tools:rules-pick", "Из канала", emoji="💬"),
                server.button("tools:rules-upload", "Из файла", emoji="📎"),
            ])
        if action == "rules-pick":
            return await respond(inter, "Выбери исходный текстовый канал.", buttons=[
                d.ui.ChannelSelect(custom_id="bs:tools:rules-channel",
                                   channel_types=[d.ChannelType.text, d.ChannelType.news]),
            ])
        if action == "rules-channel":
            channel = inter.guild.get_channel(int(inter.values[0]))
            if not isinstance(channel, d.TextChannel):
                return await respond(inter, "Выбери текстовый канал этого сервера.")
            return await rules_import(inter, channel=channel)
        if action == "rules-upload":
            return await inter.response.send_modal(title="📜 Импорт правил", custom_id="bs:tools:rules-file",
                components=[d.ui.Label("Файл правил (.txt или .md)", d.ui.FileUpload(custom_id="rules_file"))])
        if action == "rules-file":
            files = inter.resolved_values.get("rules_file", [])
            if files:
                return await rules_import(inter, file=files[0])
            return await respond(inter, "Прикрепи файл правил.")
        return
    if action in ("promote", "promote-member"):
        if not has(inter.author, REVIEWERS) and inter.author.id != inter.guild.owner_id:
            return await respond(inter, "Перевод в состав доступен штабу.")
        if action == "promote":
            return await respond(inter, "Выбери пробника для перевода.", buttons=[
                d.ui.UserSelect(custom_id="bs:tools:promote-member", placeholder="Пробник"),
            ])
        member = inter.guild.get_member(int(inter.values[0]))
        if not member:
            return await respond(inter, "Участник не найден на сервере.")
        return await promote(inter, member)
    if not await can_read_stats(inter):
        return
    if action == "stats":
        return await respond(inter, "📊 Статистика Black Squad", buttons=[
            server.button("tools:rank", "Мой уровень", emoji="⭐"),
            server.button("tools:streak", "Моя серия", emoji="🔥"),
            server.button("tools:activity", "Моя активность", emoji="📈"),
            server.button("tools:top", "Топ XP", emoji="🏆"),
            server.button("tools:profile-pick", "Найти участника", emoji="👤"),
        ])
    if action == "profile-pick":
        return await respond(inter, "Выбери, чью статистику посмотреть.", buttons=[
            d.ui.UserSelect(custom_id="bs:tools:profile", placeholder="Участник"),
        ])
    if action == "profile":
        member = inter.guild.get_member(int(inter.values[0]))
        if not member or not activity.enrolled(member):
            return await respond(inter, "Статистика доступна для участников состава.")
        total = activity.xp(member)
        item = activity.get_activity(member)
        series = streaks.state(member)
        series_text = "—"
        if series and series["hot"]:
            series_text = f"🔥{series['hot']}"
        elif series and series["cold"]:
            series_text = f"❄️{series['cold']}"
        return await respond(inter, f"{member.mention} · **{total} XP** · уровень {activity.level(total)}\n"
                                   f"Серия: {series_text}\nСообщений: {item['messages'] if item else 0}\n"
                                   f"Минут в войсе: {item['voice_ms'] // 60000 if item else 0}")
    handler = {"rank": rank, "streak": streak, "activity": activity_command, "top": top}.get(action)
    if handler:
        await handler(inter)


async def dispatch(inter, custom_id):
    if not enabled(inter.guild) or not custom_id.startswith("bs:"):
        return
    _, action, *parts = custom_id.split(":")
    if action == "tools" and parts:
        return await tools_action(inter, parts[0])
    if action == "review":
        return await recruitment.review(inter, parts[0], int(parts[1]))
    if action == "room":
        return await rooms.room_action(inter, parts[0])
    if action.startswith(("verify", "apply")):
        return await recruitment.onboarding(inter, action)


async def interaction_error(inter, error):
    logging.error("Ошибка взаимодействия", exc_info=(type(error), error, error.__traceback__))
    text = "Действие не завершено. " + str(error)[:300]
    if inter.response.is_done():
        await inter.followup.send(text, ephemeral=True)
    else:
        await respond(inter, text)


@bot.event
async def on_slash_command_error(inter, error):
    await interaction_error(inter, getattr(error, "original", error))


@bot.event
async def on_message_interaction(inter):
    try:
        await dispatch(inter, inter.component.custom_id or "")
    except Exception as error:
        await interaction_error(inter, error)


@bot.event
async def on_modal_submit(inter):
    try:
        await dispatch(inter, inter.custom_id)
    except Exception as error:
        await interaction_error(inter, error)


@bot.event
async def on_member_join(member):
    guild = member.guild
    if not enabled(guild) or member.bot or not get_config(guild).get("ready"):
        return
    activity.record(member)
    role = lookup(guild, "unverified", "roles")
    if role:
        await member.add_roles(role, reason=server.REASON)
    channel = lookup(guild, "verify", "channels")
    if channel:
        try:
            await channel.send(
                f"{member.mention}, нажми кнопку выше, чтобы изменить ник и получить доступ к серверу.",
                allowed_mentions=d.AllowedMentions(users=[member]), delete_after=60,
            )
        except d.HTTPException:
            logging.exception("Не удалось отправить приглашение к верификации для %s", member.id)
    await server.log(guild, "Участник присоединился", f"{member.mention} · ожидает верификацию")


@bot.event
async def on_member_remove(member):
    if not enabled(member.guild) or member.bot:
        return
    write("UPDATE applications SET status='left' WHERE guild=? AND applicant=? AND status IN ('open','claimed')",
          str(member.guild.id), str(member.id))
    await server.log(member.guild, "Участник покинул сервер", f"{member.mention} · {member.id}")


@bot.event
async def on_member_update(before, after):
    if not enabled(after.guild):
        return
    activity.seed(after)
    if before.roles != after.roles:
        try:
            await server.sync_clan_ping(after)
        except d.HTTPException:
            logging.exception("Не удалось обновить тег клана участнику %s", after.id)
    changes = []
    if before.nick != after.nick:
        changes.append(f"{before.display_name} → {after.display_name}")
    for title, roles in (("Выданы", set(after.roles) - set(before.roles)), ("Сняты", set(before.roles) - set(after.roles))):
        if roles:
            changes.append(f"{title} · " + ", ".join(role.mention for role in roles))
    if changes:
        await server.log(after.guild, "Участник обновлён", after.mention + "\n" + "\n".join(changes))


@bot.event
async def on_message(message):
    if enabled(message.guild):
        await activity.message_activity(message)


@bot.event
async def on_raw_message_delete(payload):
    if payload.guild_id != GUILD_ID:
        return
    message = payload.cached_message
    if message and message.author.bot:
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    author = message.author.mention if message else "Автор неизвестен"
    body = (message.content or "(без текста)") if message else "(текст недоступен)"
    await server.log(guild, "Сообщение удалено", f"{author} · <#{payload.channel_id}>\n{body}")


@bot.event
async def on_message_edit(before, after):
    if enabled(after.guild) and not after.author.bot and before.content != after.content:
        await server.log(after.guild, "Сообщение изменено", f"{after.author.mention} · {after.jump_url}"
                         f"\nБыло: {before.content}\nСтало: {after.content}")


@bot.event
async def on_raw_bulk_message_delete(payload):
    guild = bot.get_guild(payload.guild_id)
    if enabled(guild):
        await server.log(guild, "Массовое удаление сообщений", f"<#{payload.channel_id}> · удалено {len(payload.message_ids)}")


@bot.event
async def on_voice_state_update(member, before, after):
    if not enabled(member.guild):
        return
    jobs = [activity.voice_change(member, before, after)]
    if before.channel != after.channel:
        jobs.extend([
            rooms.voice_change(member, before, after),
            server.log(member.guild, "Голосовой канал", f"{member.mention} · "
                       f"{getattr(before.channel, 'mention', 'вне канала')} → "
                       f"{getattr(after.channel, 'mention', 'вне канала')}"),
        ])
        if activity.voice_eligible(member, after) or activity.voice_eligible(member, before):
            jobs.append(streaks.sync_member(member, streaks.today_msk(), True))
    results = await asyncio.gather(*jobs, return_exceptions=True)
    for result in results:
        if isinstance(result, Exception):
            logging.error("Ошибка голосового события", exc_info=(type(result), result, result.__traceback__))


AUDIT_EVENTS = {
    "channel_create": "Канал создан", "channel_update": "Канал изменён", "channel_delete": "Канал удалён",
    "overwrite_create": "Доступ к каналу добавлен", "overwrite_update": "Доступ к каналу изменён",
    "overwrite_delete": "Доступ к каналу удалён", "role_create": "Роль создана",
    "role_update": "Роль изменена", "role_delete": "Роль удалена", "kick": "Участник исключён",
    "ban": "Участник заблокирован", "unban": "Блокировка снята", "message_delete": "Модератор удалил сообщение",
    "message_bulk_delete": "Массовое удаление модератором", "member_role_update": "Модератор изменил роли",
    "member_update": "Модератор обновил участника",
}


@bot.event
async def on_guild_audit_log_entry_create(entry):
    title = AUDIT_EVENTS.get(entry.action.name)
    if enabled(entry.guild) and title:
        await server.log(entry.guild, title, f"Исполнитель · {getattr(entry.user, 'mention', 'не указан')}"
                         f"\nЦель · {entry.target}\nПричина · {entry.reason or 'не указана'}", True)


@tasks.loop(minutes=1)
async def count_voice():
    global last_streak_day
    guild = bot.get_guild(GUILD_ID)
    if guild and bot.is_ready():
        try:
            await activity.voice_tick(guild)
        except Exception:
            logging.exception("Обновление голосового актива")
        today = streaks.today_msk()
        if today != last_streak_day and get_config(guild).get("ready"):
            try:
                await streaks.daily(guild, today)
            except Exception:
                logging.exception("Обновление голосовых серий")
            else:
                last_streak_day = today
    for key, (_, expires) in list(recruitment.drafts.items()):
        if expires < time.monotonic():
            recruitment.drafts.pop(key, None)


@tasks.loop(seconds=15)
async def show_rooms_in_profile():
    global last_room_presence
    guild = bot.get_guild(GUILD_ID)
    if not guild or not bot.is_ready():
        return
    counts = rooms.public_room_counts(guild)
    if counts == last_room_presence:
        return
    try:
        await bot.change_presence(activity=d.Game(name=rooms.presence_text(counts)))
    except d.DiscordException:
        logging.exception("Не удалось обновить статус комнат")
    else:
        last_room_presence = counts


@tasks.loop(hours=1)
async def check_inactive():
    guild = bot.get_guild(GUILD_ID)
    if guild and bot.is_ready():
        try:
            await activity.inactive(guild, enforce=True)
        except Exception:
            logging.exception("Проверка неактива")


@bot.event
async def on_ready():
    global last_room_presence
    last_room_presence = None
    logging.info("Black Bot подключён как %s", bot.user)
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        joined = ", ".join(f"{item.name} (ID {item.id})" for item in bot.guilds) or "нет серверов"
        logging.error("Сервер DISCORD_GUILD_ID=%s недоступен. Black Bot подключён к серверам: %s. "
                      "Сверь ID сервера и приглашение бота.", GUILD_ID, joined)
    else:
        logging.info("Сервер для слэш-команд: %s (ID %s)", guild.name, guild.id)
    if guild and ((config := get_config(guild)).get("ready") or config.get("reset_state")):
        needs_update = config.get("reset_state") or config.get("style_version") != server.STYLE_VERSION
        if needs_update and not config_lock.locked():
            async with config_lock:
                try:
                    await rules.capture_legacy(guild)
                    await server.install(guild)
                except (d.HTTPException, ValueError):
                    logging.exception("Не удалось обновить оформление Black Squad")
        if not config_lock.locked():
            async with config_lock:
                try:
                    await server.enable_voice_activation(guild)
                except d.HTTPException:
                    logging.exception("Не удалось включить активацию по голосу на сервере")
        for restore in (rooms.restore_rooms, recruitment.restore):
            try:
                await restore(guild)
            except d.HTTPException:
                logging.exception("Восстановление %s", restore.__name__)
        already_started = count_voice.is_running()
        if not already_started:
            for member in guild.members:
                activity.seed(member)
                if activity.enrolled(member):
                    try:
                        await activity.sync_stars(member)
                    except d.HTTPException:
                        logging.exception("Проверка роли XP для %s", member.id)
    if not count_voice.is_running():
        count_voice.start()
    if not check_inactive.is_running():
        check_inactive.start()
    if not show_rooms_in_profile.is_running():
        show_rooms_in_profile.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("DISCORD_TOKEN")
    if not token or not GUILD_ID:
        raise SystemExit("Укажи DISCORD_TOKEN и DISCORD_GUILD_ID в переменных окружения или .env.")
    try:
        bot.run(token)
    except StartupConfigurationError as error:
        raise SystemExit(str(error)) from None
