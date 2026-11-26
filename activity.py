import asyncio
import logging
import hashlib
from datetime import datetime, timedelta, timezone
import time

import disnake as d

from server import REASON, log
from storage import CLAN, LEVELS, VOICE_XP_PER_MINUTE, db, get_config, has, lookup, write

DAY = 86_400_000
MINUTE = 60_000
CHAT_COOLDOWN = 3 * MINUTE
CHAT_XP = 3
CHAT_DAILY_LIMIT = 45
MSK = timezone(timedelta(hours=3))
XP_UPSERT = "INSERT INTO xp VALUES (?,?,?) ON CONFLICT(guild,user) DO UPDATE SET total=total+excluded.total"
sessions = {}
star_locks = {}


def now_ms():
    return int(time.time() * 1000)


def key(member):
    return str(member.guild.id), str(member.id)


def enrolled(member):
    return not member.bot and get_config(member.guild).get("ready") and has(member, CLAN)


def get_activity(member):
    return db.execute("SELECT * FROM activity WHERE guild=? AND user=?", key(member)).fetchone()


def record(member, messages=0, voice=0):
    write("""INSERT INTO activity(guild,user,last,messages,voice_ms) VALUES (?,?,?,?,?)
          ON CONFLICT(guild,user) DO UPDATE SET last=MAX(last,excluded.last),
          messages=messages+excluded.messages, voice_ms=voice_ms+excluded.voice_ms""",
          *key(member), now_ms(), messages, voice)


def seed(member):
    if enrolled(member):
        write("INSERT OR IGNORE INTO activity(guild,user,last) VALUES (?,?,?)", *key(member), now_ms())


def xp(member):
    row = db.execute("SELECT total FROM xp WHERE guild=? AND user=?", key(member)).fetchone()
    return row["total"] if row else 0


def level(total):
    return sum(total >= threshold for threshold in LEVELS)


async def sync_stars(member):
    identity = key(member)
    if identity in star_locks:
        return
    star_locks[identity] = True
    try:
        config = get_config(member.guild)
        while True:
            current_level = level(xp(member))
            target = lookup(member.guild, f"star{current_level}", "roles", config)
            if target and target not in member.roles:
                await member.add_roles(target, reason=REASON)
            stars = [lookup(member.guild, f"star{n}", "roles", config) for n in range(1, 6)]
            old = [role for role in stars if role != target and role in member.roles]
            if old:
                await member.remove_roles(*old, reason=REASON)
            if current_level == level(xp(member)):
                break
            member = await member.guild.fetch_member(member.id)
    finally:
        del star_locks[identity]


def claim_chat_xp(member, content, now):
    normalized = " ".join(content.casefold().split())
    if sum(c.isalnum() for c in normalized) < 5:
        return False
    day = datetime.fromtimestamp(now / 1000, MSK).date().isoformat()
    fingerprint = hashlib.sha256(normalized.encode()).hexdigest()
    identity = key(member)
    with db:
        previous = db.execute("SELECT * FROM xp_chat WHERE guild=? AND user=?", identity).fetchone()
        earned = previous["earned"] if previous and previous["day"] == day else 0
        if earned >= CHAT_DAILY_LIMIT or (previous and (
                now - previous["last"] < CHAT_COOLDOWN or fingerprint == previous["fingerprint"])):
            return False
        db.execute("""INSERT INTO xp_chat VALUES (?,?,?,?,?,?) ON CONFLICT(guild,user) DO UPDATE SET
                      day=excluded.day,earned=excluded.earned,last=excluded.last,fingerprint=excluded.fingerprint""",
                   (*identity, day, earned + CHAT_XP, now, fingerprint))
        db.execute(XP_UPSERT, (*identity, CHAT_XP))
    return True


async def message_activity(message):
    member = message.author
    if not isinstance(member, d.Member) or not enrolled(member) or message.webhook_id:
        return
    record(member, messages=1)
    if claim_chat_xp(member, message.content, now_ms()):
        await sync_stars(member)


def voice_eligible(member, state):
    return bool(state and state.channel and state.channel != member.guild.afk_channel and enrolled(member))


def earns_voice_xp(state):
    return not (state.self_deaf or state.deaf)


def record_voice_xp(member, elapsed):
    if not elapsed:
        return False
    identity = key(member)
    with db:
        item = db.execute("SELECT ms FROM xp_voice WHERE guild=? AND user=?", identity).fetchone()
        carry = item["ms"] if item else 0
        minutes, carry = divmod(carry + elapsed, MINUTE)
        db.execute("""INSERT INTO xp_voice VALUES (?,?,?) ON CONFLICT(guild,user) DO UPDATE SET ms=excluded.ms""",
                   (*identity, carry))
        if minutes:
            db.execute(XP_UPSERT, (*identity, minutes * VOICE_XP_PER_MINUTE))
    return bool(minutes)


def flush(member, now):
    last, earns_xp = sessions[key(member)]
    elapsed = max(0, now - last)
    sessions[key(member)] = now, earns_xp
    if elapsed:
        record(member, voice=elapsed)
    return earns_xp and record_voice_xp(member, elapsed)


async def voice_change(member, before, after):
    if before.channel == after.channel and earns_voice_xp(before) == earns_voice_xp(after):
        return
    identity, now = key(member), now_ms()
    earned = identity in sessions and voice_eligible(member, before) and flush(member, now)
    if voice_eligible(member, after):
        record(member)
        sessions[identity] = now, earns_voice_xp(after)
    else:
        sessions.pop(identity, None)
    if earned:
        await sync_stars(member)


async def voice_tick(guild):
    now = now_ms()
    for member in guild.members:
        if not voice_eligible(member, member.voice):
            sessions.pop(key(member), None)
            continue
        identity = key(member)
        if identity not in sessions:
            record(member)
            sessions[identity] = now, earns_voice_xp(member.voice)
            continue
        earned = flush(member, now)
        sessions[identity] = now, earns_voice_xp(member.voice)
        if earned:
            try:
                await sync_stars(member)
            except d.HTTPException:
                logging.exception("Ошибка роли XP для %s", member.id)
            await asyncio.sleep(0)


async def inactive(guild, enforce=False):
    candidates = []
    now = now_ms()
    for member in guild.members:
        if not enrolled(member):
            continue
        seed(member)
        activity = get_activity(member)
        if member.id == guild.owner_id or member.guild_permissions.administrator or has(member, ("boss",)):
            continue
        joined = int(member.joined_at.timestamp() * 1000) if member.joined_at else 0
        if now - max(activity["last"], joined) < 30 * DAY or voice_eligible(member, member.voice):
            continue
        candidates.append(member)
        if not enforce:
            continue
        try:
            actual = await guild.fetch_member(member.id)
            if (not enrolled(actual) or actual.guild_permissions.administrator or has(actual, ("boss",))
                    or now - get_activity(actual)["last"] < 30 * DAY or voice_eligible(member, member.voice)):
                continue
            await actual.kick(reason="Black Squad · 30 дней без активности")
        except d.NotFound:
            continue
        except d.HTTPException as error:
            await log(guild, "Не удалось исключить за неактив", f"{member.mention} · {error}", True)
        else:
            await log(guild, "Исключён за неактив", f"{member.mention} · последнее действие <t:{activity['last'] // 1000}:R>", True)
    return candidates
