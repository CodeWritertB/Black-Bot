import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { join } from 'node:path';

const directory = process.env.DATA_DIR || './data';
mkdirSync(directory, { recursive: true });
export const db = new DatabaseSync(join(directory, 'black-bot.sqlite'));
db.exec(`
  PRAGMA journal_mode = WAL;
  PRAGMA busy_timeout = 5000;
  CREATE TABLE IF NOT EXISTS settings (
    guild TEXT PRIMARY KEY, data TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS applications (
    id INTEGER PRIMARY KEY, guild TEXT NOT NULL, applicant TEXT NOT NULL,
    data TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'publishing', reviewer TEXT,
    channel TEXT, message TEXT
  );
  CREATE UNIQUE INDEX IF NOT EXISTS active_application ON applications(guild, applicant)
    WHERE status IN ('publishing','open','claimed','accepting','rejecting');
  CREATE TABLE IF NOT EXISTS xp (
    guild TEXT NOT NULL, user TEXT NOT NULL, total INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (guild,user)
  );
`);
export function settings(guild) {
  const row = db.prepare('SELECT data FROM settings WHERE guild=?').get(guild);
  return row ? JSON.parse(row.data) : { channels: {}, roles: {}, categories: {}, panels: {} };
}
export function save(guild, config) {
  db.prepare('INSERT INTO settings VALUES (?,?) ON CONFLICT(guild) DO UPDATE SET data=excluded.data')
    .run(guild, JSON.stringify(config));
}
