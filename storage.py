import json
import os
import sqlite3
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
directory = Path(os.getenv("DATA_DIR") or "./data")
directory.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(directory / "black-bot.sqlite")
db.row_factory = sqlite3.Row
db.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS settings (guild TEXT PRIMARY KEY, data TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS applications (
      id INTEGER PRIMARY KEY, guild TEXT NOT NULL, applicant TEXT NOT NULL,
      data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'publishing', reviewer TEXT,
      channel TEXT, message TEXT
    );
    CREATE UNIQUE INDEX IF NOT EXISTS active_application ON applications(guild, applicant)
      WHERE status IN ('publishing','open','claimed','accepting','rejecting');
    CREATE TABLE IF NOT EXISTS xp (guild TEXT, user TEXT, total INTEGER, PRIMARY KEY (guild,user));
    CREATE TABLE IF NOT EXISTS activity (
      guild TEXT, user TEXT, last INTEGER, messages INTEGER DEFAULT 0,
      voice_ms INTEGER DEFAULT 0, PRIMARY KEY (guild,user)
    );
    CREATE TABLE IF NOT EXISTS rooms (
      id TEXT PRIMARY KEY, guild TEXT, owner TEXT, locked INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS room_preferences (
      guild TEXT, owner TEXT, name TEXT, user_limit INTEGER NOT NULL DEFAULT 0,
      locked INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
      PRIMARY KEY(guild,owner)
    );
""")
# Existing Black Bot databases only have the owner and lock state.
room_columns = {row["name"] for row in db.execute("PRAGMA table_info(rooms)")}
with db:
    if "panel" not in room_columns:
        db.execute("ALTER TABLE rooms ADD COLUMN panel TEXT")
    if "hidden" not in room_columns:
        db.execute("ALTER TABLE rooms ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")


def write(sql, *args):
    with db:
        return db.execute(sql, args)


def get_config(guild):
    item = db.execute("SELECT data FROM settings WHERE guild=?", (str(guild.id),)).fetchone()
    return json.loads(item["data"]) if item else dict(roles={}, channels={}, categories={}, panels={})


def save(guild, config):
    write("INSERT INTO settings VALUES (?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data",
          str(guild.id), json.dumps(config, ensure_ascii=False))


def lookup(guild, key, kind, config=None):
    config = get_config(guild) if config is None else config
    identity = int(config[kind].get(key) or 0)
    return guild.get_role(identity) if kind == "roles" else guild.get_channel(identity)


ROLES = dict(boss="Босс", deputy="Зам", coder="Кодер", smuggler="Контрабандист",
             business="Бизнесмен", accountant="Бугалтер", raider="Рейдер",
             recruiter="Рекрутёр", pvp="ПВП", pve="ПВЕ", member="Свояк",
             trial="Пробник", guest="Кто ты, воин?", unverified="Не верифицирован",
             **{f"star{n}": "⭐" * n for n in range(1, 6)})
POSTS = ("coder", "smuggler", "business", "accountant", "raider")
REVIEWERS = ("boss", "deputy", "recruiter", *POSTS)
CLAN = ("trial", "member", *REVIEWERS)
VERIFIED = ("guest", *CLAN)
CATEGORIES = dict(gate="🔐 ВХОД", entry="📋 BLACK SQUAD · НАБОР", squad="🖤 BLACK SQUAD · КЛАН",
                  staff="🛡️ BLACK SQUAD · ШТАБ")
CHANNELS = {
    "verify": ("верификация", "gate", False, ("unverified",), False),
    "rules": ("правила", "entry", False, VERIFIED, False),
    "info": ("о-клане", "entry", False, VERIFIED, False),
    "apply": ("подать-заявку", "entry", False, VERIFIED, False),
    "interview": ("Собеседование", "entry", True, VERIFIED, True),
    "chat": ("общий", "squad", False, CLAN, True),
    "pvp": ("пвп", "squad", False, ("pvp", *REVIEWERS), True),
    "pve": ("пве", "squad", False, ("pve", *REVIEWERS), True),
    "voice": ("Штаб Black Squad", "squad", True, CLAN, True),
    "lobby": ("Создать комнату", "squad", True, CLAN, True),
    "reviews": ("заявки", "staff", False, REVIEWERS, False),
    "logs": ("логи-сервера", "staff", False, ("boss", "deputy", "coder"), False),
    "adminLogs": ("админ-логи", "staff", False, ("boss", "deputy", "coder"), False),
}
CHANNEL_EMOJI = dict(verify="🔑", rules="📜", info="🖤", apply="📝", interview="🎙️",
                     chat="💬", pvp="⚔️", pve="🌾", voice="🎧", lobby="➕",
                     reviews="📨", logs="📋", adminLogs="🛠️")
ROLE_EMOJI = dict(boss="👑", deputy="🛡️", coder="💻", smuggler="📦", business="💼",
                  accountant="📊", raider="💥", recruiter="📣", pvp="⚔️", pve="🌾",
                  member="🖤", trial="⏳", guest="👤", unverified="🔒")


def channel_name(key):
    return f"{CHANNEL_EMOJI[key]}┃{CHANNELS[key][0]}"


def role_name(key):
    return f"{ROLE_EMOJI[key]} ┃ {ROLES[key]}" if key in ROLE_EMOJI else ROLES[key]


FIELDS = {
    "identity": ("Имя и возраст", 64), "nick": ("Игровой никнейм", 32),
    "hours": ("Количество часов в ROB RUN", 10), "activity": ("Время игровой активности в сутки", 100),
    "specialty": ("Специализация", 20), "previous": ("Предыдущие кланы", 300),
    "expectations": ("Какие ожидания от BLACK SQUAD", 600),
}
LEVELS = (100, 400, 900, 1600, 2500)


def has(member, keys, config=None):
    config = get_config(member.guild) if config is None else config
    identities = {int(config["roles"].get(key) or 0) for key in keys}
    return any(role.id in identities for role in member.roles)
