"""Daily voice streaks adapted from Hunter Bot."""
import logging
import re
from datetime import date, datetime, timedelta

import disnake as d

from activity import MSK, enrolled, voice_eligible
from storage import db, write

SUFFIX = re.compile(r"\s*(?:(?:🔥|❄)\ufe0f?\s*)+\d+$")
LIMIT = 32


def today_msk():
    return datetime.now(MSK).date()


def state(member):
    return db.execute("SELECT * FROM voice_streaks WHERE guild=? AND user=?",
                      (str(member.guild.id), str(member.id))).fetchone()


def advance(last_day, hot, cold, today, active):
    yesterday = today - timedelta(days=1)
    last_day = last_day or yesterday
    if last_day > today:
        return last_day, hot, cold
    missed = (yesterday - last_day).days
    if missed > 0:
        hot, cold, last_day = 0, cold + missed, yesterday
    if active and (last_day != today or not hot):
        hot, cold, last_day = hot + 1, 0, today
    return last_day, hot, cold


def build_nick(base, hot, cold):
    base = SUFFIX.sub("", base).strip()
    suffix = f" 🔥{hot}" if hot else f" ❄️{cold}" if cold else ""
    room = LIMIT - len(suffix)
    if len(base) > room and " | " in base:
        nick, name = base.split(" | ", 1)
        name = name[:room - 4].rstrip()
        nick = nick[:room - len(name) - 3].rstrip()
        return f"{nick} | {name}{suffix}"
    return base[:room].rstrip() + suffix


async def sync_member(member, today, active):
    if not enrolled(member):
        return
    row = state(member)
    last = date.fromisoformat(row["day"]) if row else None
    date_next, hot, cold = advance(last, row["hot"] if row else 0, row["cold"] if row else 0, today, active)
    shown = member.nick or member.display_name
    if row and shown == row["applied_nick"] and row["base_nick"]:
        base = row["base_nick"]
    else:
        base = SUFFIX.sub("", shown).strip()
    updated = build_nick(base, hot, cold)
    applied = row["applied_nick"] if row else None
    write("""INSERT INTO voice_streaks VALUES (?,?,?,?,?,?,?)
          ON CONFLICT(guild,user) DO UPDATE SET day=excluded.day,hot=excluded.hot,cold=excluded.cold,
          base_nick=excluded.base_nick,applied_nick=excluded.applied_nick""",
          str(member.guild.id), str(member.id), date_next.isoformat(), hot, cold, base, applied)
    if updated == shown or member.id == member.guild.owner_id:
        return
    try:
        await member.edit(nick=updated, reason="Голосовая серия Black Squad")
    except d.HTTPException:
        logging.warning("Не удалось изменить ник для голосовой серии участника %s", member.id)
    else:
        write("UPDATE voice_streaks SET applied_nick=? WHERE guild=? AND user=?",
              updated, str(member.guild.id), str(member.id))


async def daily(guild, today):
    for member in guild.members:
        if enrolled(member):
            await sync_member(member, today, voice_eligible(member, member.voice))
