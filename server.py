import logging

import disnake as d

import rules

from storage import CATEGORIES, CHANNELS, CLAN, POSTS, ROLE_COLORS, ROLES, VERIFIED, channel_name, db, get_config, has, lookup, role_name, save

REASON = "Black Bot · Black Squad"
STYLE_VERSION = 11
REQUIRED = (
    "manage_roles", "manage_channels", "manage_nicknames", "change_nickname", "kick_members",
    "view_audit_log", "move_members", "connect", "speak", "view_channel", "send_messages",
    "read_message_history", "embed_links", "attach_files", "use_application_commands",
    "send_messages_in_threads", "create_public_threads", "create_private_threads",
)
CODER_PERMISSIONS = d.Permissions(
    manage_guild=True, manage_roles=True, manage_channels=True, view_audit_log=True,
    manage_messages=True, manage_threads=True, manage_webhooks=True,
    manage_guild_expressions=True, manage_events=True, manage_nicknames=True,
    kick_members=True, ban_members=True, moderate_members=True,
    move_members=True, mute_members=True, deafen_members=True,
)
DEPUTY_PERMISSIONS = d.Permissions(manage_roles=True, move_members=True)
STAFF_PERMISSIONS = {"coder": CODER_PERMISSIONS, "deputy": DEPUTY_PERMISSIONS}




# Discord displays only the highest separated role a member has.
ROLE_ORDER = (
    "boss", "deputy", "coder", "smuggler", "business", "accountant", "raider", "recruiter",
    "star5", "star4", "star3", "star2", "star1", "member", "trial", "pvp", "pve",
    "guest", "unverified", "clan_ping",
)


async def order_roles(guild, configured):
    all_roles = sorted(await guild.fetch_roles())
    by_id = {role.id: role for role in all_roles}
    ranked = [by_id[configured[key].id] for key in ROLE_ORDER]
    current = sorted(ranked, reverse=True)
    if current == ranked:
        return
    # Use the existing positions of Black Squad roles, leaving unrelated roles outside the edit.
    slots = [role.position for role in current]
    if len(set(slots)) != len(slots):
        ids = {role.id for role in ranked}
        slots = [i for i, role in reversed(list(enumerate(all_roles))) if role.id in ids]
    await guild.edit_role_positions(positions={role: slot for role, slot in zip(ranked, slots)}, reason=REASON)


async def sync_clan_ping(member, config=None, role=None):
    if member.bot:
        return
    config = get_config(member.guild) if config is None else config
    role = role or lookup(member.guild, "clan_ping", "roles", config)
    if role is None:
        return
    in_clan = has(member, CLAN, config)
    already = role in member.roles
    if in_clan and not already:
        await member.add_roles(role, reason=REASON)
    elif already and not in_clan:
        await member.remove_roles(role, reason=REASON)


def can_configure(member):
    permissions = member.guild_permissions
    return permissions.administrator or (permissions.manage_guild and has(member, ("coder",)))


PANEL_EMOJI = {"verify": "🔑", "apply": "📝", "tools:stats": "📊", "tools:promote": "⬆️", "tools:admin": "🛠️"}
PANELS = {
    "adminLogs": ("**🛠️ Управление Black Squad**\nНастройки каналов, аудит и импорт правил.", "tools:admin", "Открыть управление"),
    "chat": ("**📊 Статистика Black Squad**\nУровень, XP, голосовая серия и рейтинг клана.", "tools:stats", "Статистика"),
    "reviews": ("**📨 Набор Black Squad**\nЗаявки рассматриваются кнопками под карточками.", "tools:promote", "Перевести пробника"),
    "verify": ("**🔐 Верификация Black Squad**\nНажми кнопку и введи **Ник | Имя**, например **CodeWriterB | Борис**.", "verify", "Изменить никнейм"),
    "rules": ("**📜 Правила Black Squad**\n1. Уважай участников. Без травли, угроз и дискриминации.\n"
              "2. Без спама, мошенничества, читов и публикации чужих данных.\n"
              "3. Соблюдай договорённости отряда. Споры решай через штаб.\n"
              "4. Через 30 дней без сообщений и голосовой активности бот исключает участников клана.", None, None),
    "info": ("**🖤 BLACK SQUAD · ROB RUN**\nКлан для ПВП, рейдов и ПВЕ-фарма. Штаб координирует бои "
             "за бизнес-территории и развитие в течение вайпа. Новички начинают с испытательного срока.", None, None),
    "apply": ("**📝 Вступить в BLACK SQUAD**\nЗаполни заявку в два шага и дождись приглашения в один из войсов для собеседования.", "apply", "Заполнить заявку"),
}


def button(key, label, style=d.ButtonStyle.primary, *, emoji=None):
    return d.ui.Button(custom_id=f"bs:{key}", label=label, style=style, emoji=emoji)


async def log(guild, title, description, staff=False):
    config = get_config(guild)
    if config.get("reset_state"):
        return
    channel = lookup(guild, "adminLogs" if staff else "logs", "channels", config)
    if channel:
        try:
            await channel.send(embed=d.Embed(title=f"{'🛠️' if staff else '📋'} {title}", description=description[:4096],
                                             color=0xa98658 if staff else 0x474e5b, timestamp=d.utils.utcnow()))
        except d.HTTPException:
            import logging
            logging.exception("Ошибка журнала %s", title)


def permissions(guild, config, key):
    _, _, voice, audience, writable = CHANNELS[key]
    result = {
        guild.default_role: d.PermissionOverwrite(view_channel=False),
        guild.me: d.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True,
                                         attach_files=True, embed_links=True),
    }
    for name in set(audience):
        role = lookup(guild, name, "roles", config)
        if role is None:
            raise ValueError(f"Роль «{ROLES[name]}» отсутствует. Выполни /setup.")
        announces = key == "announcements" and name in ("boss", "deputy")
        can_write = writable or announces
        result[role] = d.PermissionOverwrite(
            view_channel=True, read_message_history=True, use_application_commands=True,
            send_messages=can_write,
            mention_everyone=(True if announces else None) if key == "announcements" else None,
            send_messages_in_threads=None if writable else False, create_public_threads=None if writable else False,
            create_private_threads=None if writable else False, connect=True if voice else None, speak=True if voice else None,
            stream=True if key.startswith("interview") else None,
        )
    if key == "announcements":
        result[guild.default_role].send_messages = False
        result[guild.default_role].mention_everyone = False
    return result


async def publish(guild, config, key, channel):
    if key not in PANELS:
        return
    text, action, label = PANELS[key]
    buttons = [button(action, label, emoji=PANEL_EMOJI[action])] if action else []
    if key == "reviews":
        buttons.append(button("tools:invite", "Пригласить на сервер", emoji="🔗"))
    old = config["panels"].get(key)
    original = guild.get_channel(int(old["channel"])) if old else None
    message = None
    if original:
        try:
            message = await original.fetch_message(int(old["message"]))
        except d.NotFound:
            pass
    if key == "rules" and config.get("rules_text"):
        await rules.publish(guild, config, channel)
        if message and message.author.id == guild.me.id:
            try:
                await message.delete()
            except d.NotFound:
                pass
        config["panels"].pop(key, None)
        save(guild, config)
        return
    if message and message.author.id == guild.me.id and original.id == channel.id:
        await message.edit(content=text, components=buttons)
        return
    new = await channel.send(text, components=buttons)
    config["panels"][key] = {"channel": str(channel.id), "message": str(new.id)}
    save(guild, config)
    if message and message.author.id == guild.me.id:
        await message.edit(components=[])


def category_overwrites(guild, config, key):
    overrides = {
        guild.default_role: d.PermissionOverwrite(view_channel=False),
        guild.me: d.PermissionOverwrite(view_channel=True),
    }
    if key == "gate":
        role = lookup(guild, "unverified", "roles", config)
        overrides[role] = d.PermissionOverwrite(view_channel=True)
    return overrides


async def seed_verification(guild, config):
    role = lookup(guild, "unverified", "roles", config)
    members = [
        member for member in guild.members
        if not member.bot and not member.guild_permissions.administrator
        and not has(member, ("unverified", *VERIFIED), config)
    ]
    for member in members:
        try:
            await member.add_roles(role, reason=REASON)
        except d.HTTPException:
            logging.exception("Не удалось выдать роль верификации участнику %s", member.id)


async def enable_voice_activation(guild):
    config = get_config(guild)
    if config.get("voice_activation_enabled"):
        return
    default = guild.default_role
    if not default.permissions.use_voice_activation:
        permissions = default.permissions
        permissions.use_voice_activation = True
        await default.edit(permissions=permissions, reason=REASON)

    categories = list(guild.categories)
    # Editing the category also updates its synced channels.
    channels = [channel for channel in (*guild.voice_channels, *guild.stage_channels)
                if not channel.permissions_synced]
    for channel in (*categories, *channels):
        for target, overwrite in channel.overwrites.items():
            if overwrite.use_voice_activation is False:
                overwrite.use_voice_activation = None
                await channel.set_permissions(target, overwrite=overwrite, reason=REASON)
    config["voice_activation_enabled"] = True
    save(guild, config)


async def install(guild):
    missing = [name for name in REQUIRED if not getattr(guild.me.guild_permissions, name)]
    if missing:
        raise ValueError("Не хватает прав: " + ", ".join(missing))
    config = get_config(guild)
    configured = {}
    for key, original_name in ROLES.items():
        name = role_name(key)
        color = d.Color(ROLE_COLORS[key])
        separated = key != "clan_ping"
        role = lookup(guild, key, "roles", config) or next((item for item in guild.roles if item.name in (name, original_name) and not item.managed), None)
        if role is None:
            role = await guild.create_role(name=name, color=color, hoist=separated, reason=REASON)
        if not role.is_assignable():
            raise ValueError(f"Подними управляемую роль Black Bot выше «{name}».")
        changes = {}
        if role.name != name:
            changes["name"] = name
        if role.color != color:
            changes["color"] = color
        if role.hoist != separated:
            changes["hoist"] = separated
        if key == "clan_ping" and role.mentionable:
            changes["mentionable"] = False
        if key in STAFF_PERMISSIONS:
            grantable = STAFF_PERMISSIONS[key].value & guild.me.guild_permissions.value
            updated = role.permissions.value | grantable
            if updated != role.permissions.value:
                changes["permissions"] = d.Permissions(updated)
        if changes:
            role = await role.edit(reason=REASON, **changes)
        configured[key] = role
        config["roles"][key] = str(role.id)
        save(guild, config)
    await order_roles(guild, configured)
    label = guild.get_role(int(config.get("botRole") or 0))
    if label is None:
        label = next((r for r in guild.roles if r.name in ("Бот", "🤖 ┃ Бот") and not r.managed and not r.permissions.value), None)
    bot_color = d.Color(ROLE_COLORS["botRole"])
    if label is None:
        label = await guild.create_role(name="🤖 ┃ Бот", color=bot_color, hoist=True, reason=REASON)
    if label.is_assignable() and (label.name != "🤖 ┃ Бот" or label.color != bot_color or not label.hoist):
        label = await label.edit(name="🤖 ┃ Бот", color=bot_color, hoist=True, reason=REASON)
    if label not in guild.me.roles:
        await guild.me.add_roles(label, reason=REASON)
    config["botRole"] = str(label.id)
    save(guild, config)
    if guild.me.nick != "Black Bot":
        await guild.me.edit(nick="Black Bot", reason=REASON)
    categories = {}
    for key, name in CATEGORIES.items():
        category = lookup(guild, key, "categories", config)
        if category is None:
            names = (name, name.split(" ", 1)[1])
            matches = [item for item in guild.categories if item.name in names
                       and str(item.id) not in config["categories"].values()]
            if len(matches) == 1:
                category = matches[0]
        overwrites = category_overwrites(guild, config, key)
        if category is None:
            category = await guild.create_category(name, overwrites=overwrites, reason=REASON)
        elif category.name != name or category.overwrites != overwrites:
            await category.edit(name=name, overwrites=overwrites, reason=REASON)
        categories[key] = category
        config["categories"][key] = str(category.id)
        save(guild, config)
    await seed_verification(guild, config)
    for member in guild.members:
        try:
            await sync_clan_ping(member, config, configured["clan_ping"])
        except d.HTTPException:
            logging.exception("Не удалось обновить тег клана участнику %s", member.id)
    for key, (original_name, category, voice, _, _) in CHANNELS.items():
        name = channel_name(key)
        channel = lookup(guild, key, "channels", config)
        parent = categories[category]
        if channel is None:
            expected = d.VoiceChannel if voice else d.TextChannel
            matches = [item for item in parent.channels
                       if isinstance(item, expected) and item.name in (name, original_name)
                       and str(item.id) not in config["channels"].values()]
            if len(matches) == 1:
                channel = matches[0]
        overwrites = permissions(guild, config, key)
        if channel:
            gate = parent if key == "verify" else None
            placement = {"category": gate} if gate and channel.category_id != gate.id else {}
            if channel.name != name or channel.overwrites != overwrites or placement:
                await channel.edit(name=name, overwrites=overwrites, reason=REASON, **placement)
        else:
            create = guild.create_voice_channel if voice else guild.create_text_channel
            channel = await create(name, category=parent,
                                   overwrites=overwrites, reason=REASON)
        config["channels"][key] = str(channel.id)
        save(guild, config)
        await publish(guild, config, key, channel)
    for key in ("pvp", "pve"):
        config["channels"].pop(key, None)
    config.pop("reset_state", None)
    config["ready"] = True
    config["style_version"] = STYLE_VERSION
    save(guild, config)
    await enable_voice_activation(guild)


async def assign(guild, key, channel):
    config = get_config(guild)
    if not config.get("ready"):
        raise ValueError("Сначала выполни /setup.")
    expected = d.VoiceChannel if CHANNELS[key][2] else d.TextChannel
    if not isinstance(channel, expected):
        raise ValueError("Канал неверного типа.")
    if any(other != key and int(identity) == channel.id for other, identity in config["channels"].items()):
        raise ValueError("Этот канал уже назначен.")
    if key == "reviews" and str(channel.id) != str(config["channels"].get(key)) and db.execute(
        "SELECT 1 FROM applications WHERE guild=? AND status IN ('publishing','open','claimed','accepting','rejecting')",
        (str(guild.id),),
    ).fetchone():
        raise ValueError("Перед переносом канала закрой активные заявки.")
    kwargs = {"category": lookup(guild, "gate", "categories", config)} if key == "verify" else {}
    await channel.edit(name=channel_name(key), overwrites=permissions(guild, config, key), reason=REASON, **kwargs)
    config["channels"][key] = str(channel.id)
    save(guild, config)
    await publish(guild, config, key, channel)


async def audit(guild):
    config = get_config(guild)
    findings = []
    if not config.get("ready"):
        findings.append("Настройка /setup ещё не завершена.")
    if guild.id == rules.LEGACY_GUILD and not config.get("rules_text"):
        findings.append("Текст старых правил ещё не сохранён. Открой «Управление» → «Импорт правил».")
    gate = lookup(guild, "gate", "categories", config)
    unverified = lookup(guild, "unverified", "roles", config)
    if config.get("ready") and gate and unverified and gate.overwrites != category_overwrites(guild, config, "gate"):
        findings.append(f"Проверь доступ к категории: {gate.name}")
    findings.extend(f"Нет права бота: {name}" for name in REQUIRED if not getattr(guild.me.guild_permissions, name))
    for key, label in {**ROLES, "botRole": "Бот"}.items():
        role = (guild.get_role(int(config.get(key) or 0)) if key == "botRole"
                else lookup(guild, key, "roles", config))
        if role is None or not role.is_assignable():
            findings.append(f"Роль отсутствует или выше бота: {label}")
        if role and key in STAFF_PERMISSIONS and not role.permissions.administrator:
            missing = [name for name, requested in STAFF_PERMISSIONS[key]
                       if requested and not getattr(role.permissions, name)]
            if missing:
                findings.append(f"Роли «{label}» не выданы права: " + ", ".join(missing))
        if role and role.permissions.administrator and key not in ("boss", "coder"):
            findings.append(f"Проверь право администратора: {label}")
    for key in STAFF_PERMISSIONS:
        manager = lookup(guild, key, "roles", config)
        if manager is None:
            continue
        managed = [lookup(guild, target, "roles", config)
                   for target in ("recruiter", "member", "trial", "pvp", "pve", "guest", "unverified")]
        above = [role.name for role in managed if role is not None and role >= manager]
        if above:
            findings.append(f"Подними «{manager.name}» выше ролей: " + ", ".join(above))
    known = set()
    for key, (name, _, voice, _, _) in CHANNELS.items():
        channel = lookup(guild, key, "channels", config)
        if not isinstance(channel, d.VoiceChannel if voice else d.TextChannel):
            findings.append(f"Проверь канал: {name}")
            continue
        known.add(channel.id)
        if config.get("ready"):
            try:
                if channel.overwrites != permissions(guild, config, key):
                    findings.append(f"Проверь доступ: {channel.mention}")
            except ValueError:
                pass
        if key in PANELS:
            panel = config["panels"].get(key)
            if key == "rules" and config.get("rules_text"):
                pages = config["panels"].get("rules_pages", {})
                ids = pages.get("messages", [])
                panel = {"channel": pages["channel"], "message": ids[0]} if ids else None
            try:
                message = await channel.fetch_message(int(panel["message"])) if panel and int(panel["channel"]) == channel.id else None
            except d.HTTPException:
                message = None
            if not message or message.author.id != guild.me.id or (PANELS[key][1] and not message.components):
                findings.append(f"Проверь панель: {name}")
    temporary = {int(item["id"]) for item in db.execute("SELECT id FROM rooms WHERE guild=?", (str(guild.id),))}
    extra = [channel.mention for channel in guild.channels
             if not isinstance(channel, d.CategoryChannel) and channel.id not in known | temporary]
    if extra:
        findings.append("Каналы вне настройки: " + ", ".join(extra))
    for member in guild.members:
        if member.bot:
            continue
        if not has(member, ("unverified", "guest", *CLAN), config):
            findings.append(f"Без роли доступа: {member.mention}")
        count = sum(has(member, (key,), config) for key in POSTS)
        if count != int(has(member, ("deputy",), config)):
            findings.append(f"Проверь роль «Зам» и должность: {member.mention}")
    return findings
