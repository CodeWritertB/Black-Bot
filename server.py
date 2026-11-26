import logging

import disnake as d

import rules

from storage import CATEGORIES, CHANNELS, CLAN, POSTS, ROLES, VERIFIED, channel_name, db, get_config, has, lookup, role_name, save

REASON = "Black Bot · Black Squad"
STYLE_VERSION = 1
REQUIRED = (
    "manage_roles", "manage_channels", "manage_nicknames", "change_nickname", "kick_members",
    "view_audit_log", "move_members", "connect", "speak", "view_channel", "send_messages",
    "read_message_history", "embed_links", "attach_files", "use_application_commands",
    "send_messages_in_threads", "create_public_threads", "create_private_threads",
)
PANELS = {
    "verify": ("**🔐 Верификация Black Squad**\nНажми кнопку и введи **Ник | Имя**, например **CodeWriterB | Борис**.", "verify", "Изменить никнейм"),
    "rules": ("**📜 Правила Black Squad**\n1. Уважай участников. Без травли, угроз и дискриминации.\n"
              "2. Без спама, мошенничества, читов и публикации чужих данных.\n"
              "3. Соблюдай договорённости отряда. Споры решай через штаб.\n"
              "4. Через 30 дней без сообщений и голосовой активности бот исключает участников клана.", None, None),
    "info": ("**🖤 BLACK SQUAD · ROB RUN**\nКлан для ПВП, рейдов и ПВЕ-фарма. Штаб координирует бои "
             "за бизнес-территории и развитие в течение вайпа. Новички начинают с испытательного срока.", None, None),
    "apply": ("**📝 Вступить в BLACK SQUAD**\nЗаполни заявку в два шага и зайди в войс «Собеседование».", "apply", "Заполнить заявку"),
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
        result[role] = d.PermissionOverwrite(
            view_channel=True, read_message_history=True, use_application_commands=True, send_messages=writable,
            send_messages_in_threads=None if writable else False, create_public_threads=None if writable else False,
            create_private_threads=None if writable else False, connect=True if voice else None, speak=True if voice else None,
        )
    return result


async def publish(guild, config, key, channel):
    if key not in PANELS:
        return
    text, action, label = PANELS[key]
    if key == "rules" and config.get("rules_source"):
        text = "**📜 Правила Black Squad**\nПравила перенесены из прежнего канала. Читай их ниже."
    buttons = [button(action, label, emoji={"verify": "🔑", "apply": "📝"}[action])] if action else []
    old = config["panels"].get(key)
    original = guild.get_channel(int(old["channel"])) if old else None
    message = None
    if original:
        try:
            message = await original.fetch_message(int(old["message"]))
        except d.NotFound:
            pass
    if message and message.author.id == guild.me.id and original.id == channel.id:
        await message.edit(content=text, components=buttons)
        if key == "rules" and config.get("rules_text"):
            await rules.publish(guild, config, channel)
        return
    new = await channel.send(text, components=buttons)
    config["panels"][key] = {"channel": str(channel.id), "message": str(new.id)}
    save(guild, config)
    if message and message.author.id == guild.me.id:
        await message.edit(components=[])
    if key == "rules" and config.get("rules_text"):
        await rules.publish(guild, config, channel)


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


async def install(guild):
    missing = [name for name in REQUIRED if not getattr(guild.me.guild_permissions, name)]
    if missing:
        raise ValueError("Не хватает прав: " + ", ".join(missing))
    config = get_config(guild)
    for key, original_name in ROLES.items():
        name = role_name(key)
        role = lookup(guild, key, "roles", config) or next((item for item in guild.roles if item.name in (name, original_name) and not item.managed), None)
        if role is None:
            role = await guild.create_role(name=name, reason=REASON)
        if not role.is_assignable():
            raise ValueError(f"Подними управляемую роль Black Bot выше «{name}».")
        if role.name != name:
            role = await role.edit(name=name, reason=REASON)
        config["roles"][key] = str(role.id)
        save(guild, config)
    label = guild.get_role(int(config.get("botRole") or 0))
    if label is None:
        label = next((r for r in guild.roles if r.name in ("Бот", "🤖 ┃ Бот") and not r.managed and not r.permissions.value), None)
    if label is None:
        label = await guild.create_role(name="🤖 ┃ Бот", reason=REASON)
    if label.name != "🤖 ┃ Бот" and label.is_assignable():
        label = await label.edit(name="🤖 ┃ Бот", reason=REASON)
    if label not in guild.me.roles:
        await guild.me.add_roles(label, reason=REASON)
    config["botRole"] = str(label.id)
    save(guild, config)
    if guild.me.nick != "Black Bot":
        await guild.me.edit(nick="Black Bot", reason=REASON)
    for key, name in CATEGORIES.items():
        category = lookup(guild, key, "categories", config)
        overwrites = category_overwrites(guild, config, key)
        if category is None:
            category = await guild.create_category(name, overwrites=overwrites, reason=REASON)
        elif category.name != name or category.overwrites != overwrites:
            await category.edit(name=name, overwrites=overwrites, reason=REASON)
        config["categories"][key] = str(category.id)
        save(guild, config)
    await seed_verification(guild, config)
    for key, (_, category, voice, _, _) in CHANNELS.items():
        name = channel_name(key)
        channel = lookup(guild, key, "channels", config)
        overwrites = permissions(guild, config, key)
        if channel:
            gate = lookup(guild, "gate", "categories", config) if key == "verify" else None
            placement = {"category": gate} if gate and channel.category_id != gate.id else {}
            if channel.name != name or channel.overwrites != overwrites or placement:
                await channel.edit(name=name, overwrites=overwrites, reason=REASON, **placement)
        else:
            create = guild.create_voice_channel if voice else guild.create_text_channel
            channel = await create(name, category=lookup(guild, category, "categories", config),
                                   overwrites=overwrites, reason=REASON)
        config["channels"][key] = str(channel.id)
        save(guild, config)
        await publish(guild, config, key, channel)
    config["ready"] = True
    config["style_version"] = STYLE_VERSION
    save(guild, config)


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
        findings.append("Текст старых правил ещё не сохранён. Используй /rules_import.")
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
        if role and role.permissions.administrator and key not in ("boss", "coder"):
            findings.append(f"Проверь право администратора: {label}")
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
