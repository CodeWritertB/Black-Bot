"""Store the rules text before replacing its Discord channel."""
import disnake as d

from storage import get_config, lookup, save

LEGACY_GUILD = 1575305374763061248
LEGACY_RULES_CHANNEL = 1575305952070991992


def sections(message):
    if message.content.strip():
        yield message.content
    for embed in message.embeds:
        if embed.title:
            yield f"**{embed.title}**"
        if embed.description:
            yield embed.description
        for field in embed.fields:
            yield f"**{field.name}**\n{field.value}"
        if embed.footer.text:
            yield embed.footer.text


def split_text(parts, limit=3800):
    output = []
    for part in parts:
        while len(part) > limit:
            boundary = part.rfind("\n", 0, limit)
            if boundary <= 0:
                boundary = limit
            output.append(part[:boundary])
            part = part[boundary:].lstrip("\n")
        if part:
            if output and len(output[-1]) + len(part) + 2 <= limit:
                output[-1] += "\n\n" + part
            else:
                output.append(part)
    return output


async def capture(guild, source):
    parts = []
    async for message in source.history(limit=None, oldest_first=True):
        if message.type not in (d.MessageType.default, d.MessageType.reply):
            continue
        parts.extend(sections(message))
    return store(guild, str(source.id), parts)


def store(guild, source, parts):
    texts = split_text(parts)
    if not texts:
        raise ValueError("В выбранном канале нет правил в тексте или embed.")
    config = get_config(guild)
    config["rules_source"] = source
    config["rules_text"] = texts
    save(guild, config)
    return config


async def capture_legacy(guild):
    config = get_config(guild)
    if config.get("rules_text") or guild.id != LEGACY_GUILD:
        return config
    source = guild.get_channel(LEGACY_RULES_CHANNEL)
    if source is None:
        try:
            source = await guild.fetch_channel(LEGACY_RULES_CHANNEL)
        except (d.NotFound, d.Forbidden):
            return config
    if isinstance(source, d.TextChannel):
        try:
            return await capture(guild, source)
        except (ValueError, d.Forbidden):
            pass
    return config


async def publish(guild, config, channel):
    tracked = config["panels"].get("rules_pages", {})
    ids = list(tracked.get("messages", [])) if tracked.get("channel") == str(channel.id) else []
    tracked = {"channel": str(channel.id), "messages": ids}
    config["panels"]["rules_pages"] = tracked
    texts = config.get("rules_text", [])
    for index, text in enumerate(texts):
        embed = d.Embed(title="📜 Правила Black Squad" if index == 0 else "📜 Правила · продолжение",
                        description=text, color=0xa98658)
        message = None
        if index < len(ids):
            try:
                message = await channel.fetch_message(int(ids[index]))
            except d.NotFound:
                pass
        if message:
            if message.author.id != guild.me.id:
                raise ValueError("Сохранённая панель правил принадлежит другому приложению.")
            if not message.embeds or message.embeds[0].to_dict() != embed.to_dict():
                await message.edit(embed=embed, allowed_mentions=d.AllowedMentions.none())
        else:
            message = await channel.send(embed=embed, allowed_mentions=d.AllowedMentions.none())
            if index < len(ids):
                ids[index] = str(message.id)
            else:
                ids.append(str(message.id))
            save(guild, config)
    for message_id in ids[len(texts):]:
        try:
            message = await channel.fetch_message(int(message_id))
            if message.author.id == guild.me.id:
                await message.delete()
        except d.NotFound:
            pass
    del ids[len(texts):]
    save(guild, config)
