import { ChannelType } from 'discord.js';
import { randomInt } from 'node:crypto';
import { db, settings } from './store.js';
import { clan, levels } from './settings.js';
import { admin, has, log, reason } from './server.js';
import { expireDrafts } from './recruitment.js';

const day = 86_400_000;
const cutoff = 30 * day;
const fiveMinutes = 300_000;
const sessions = new Map();
const cooldowns = new Map();
const syncing = new Set();
db.exec(`CREATE TABLE IF NOT EXISTS activity (
  guild TEXT NOT NULL, user TEXT NOT NULL, last INTEGER NOT NULL,
  messages INTEGER NOT NULL DEFAULT 0, voice_ms INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (guild,user)
)`);
const record = db.prepare(`INSERT INTO activity(guild,user,last,messages,voice_ms) VALUES (?,?,?,?,?)
  ON CONFLICT(guild,user) DO UPDATE SET last=MAX(last,excluded.last),
  messages=messages+excluded.messages,voice_ms=voice_ms+excluded.voice_ms`);
const getActivity = db.prepare('SELECT * FROM activity WHERE guild=? AND user=?');
const getXp = db.prepare('SELECT total FROM xp WHERE guild=? AND user=?');
const setXp = db.prepare(`INSERT INTO xp VALUES (?,?,?) ON CONFLICT(guild,user) DO UPDATE SET total=total+excluded.total RETURNING total`);
const seed = db.prepare('INSERT OR IGNORE INTO activity(guild,user,last) VALUES (?,?,?)');
const levelOf = total => levels.filter(threshold => total >= threshold).length;
const keyOf = member => member.guild.id + ':' + member.id;
const enrolled = (member, config = settings(member.guild.id)) => config.ready && !member.user.bot && has(member, clan, config);
const eligibleVoice = state => state?.member && enrolled(state.member) &&
  [ChannelType.GuildVoice, ChannelType.GuildStageVoice].includes(state.channel?.type) && state.channelId !== state.guild.afkChannelId;

export async function syncStars(member) {
  const key = keyOf(member);
  if (syncing.has(key)) return;
  syncing.add(key);
  try {
    const config = settings(member.guild.id);
    const stars = levels.map((_, index) => config.roles['star' + (index + 1)]).filter(Boolean);
    for (;;) {
      const level = levelOf(getXp.get(member.guild.id, member.id)?.total || 0);
      const target = config.roles['star' + level];
      if (target && !member.roles.cache.has(target)) await member.roles.add(target, reason);
      for (const id of stars) {
        if (id !== target && member.roles.cache.has(id)) await member.roles.remove(id, reason);
      }
      if (level === levelOf(getXp.get(member.guild.id, member.id)?.total || 0)) break;
      member = await member.guild.members.fetch(member.id);
    }
  } finally {
    syncing.delete(key);
  }
}

async function award(member, amount) {
  const { total } = setXp.get(member.guild.id, member.id, amount);
  await syncStars(member);
  return total;
}

export async function messageActivity(message) {
  if (!message.member || !enrolled(message.member)) return;
  const now = Date.now();
  record.run(message.guild.id, message.author.id, now, 1, 0);
  const key = keyOf(message.member);
  if (now - (cooldowns.get(key) || 0) < 60_000) return;
  cooldowns.set(key, now);
  await award(message.member, randomInt(10, 26));
}

function flushVoice(member, session, now) {
  const elapsed = now - session.last;
  if (elapsed <= 0) return 0;
  record.run(member.guild.id, member.id, now, 0, elapsed);
  session.last = now;
  session.remainder += elapsed;
  const batches = Math.floor(session.remainder / fiveMinutes);
  session.remainder %= fiveMinutes;
  return 10 * batches;
}

export async function voiceActivity(before, after) {
  const member = after.member || before.member;
  if (!member || before.channelId === after.channelId) return;
  const key = keyOf(member);
  const now = Date.now();
  const previous = sessions.get(key);
  const earned = previous && enrolled(member) ? flushVoice(member, previous, now) : 0;
  if (eligibleVoice(after)) {
    record.run(member.guild.id, member.id, now, 0, 0);
    sessions.set(key, { last: now, remainder: previous?.remainder || 0 });
  } else {
    sessions.delete(key);
  }
  if (earned) await award(member, earned);
}

export function trackMember(member) {
  if (enrolled(member)) seed.run(member.guild.id, member.id, Date.now());
}
export function resetJoin(member) {
  record.run(member.guild.id, member.id, Date.now(), 0, 0);
}
export function describe(member) {
  const item = getActivity.get(member.guild.id, member.id);
  const total = getXp.get(member.guild.id, member.id)?.total || 0;
  const level = levelOf(total);
  return {
    rank: member + ' · ' + (level ? '⭐'.repeat(level) : '0 звёзд') + ' · ' + total + ' XP' + (level < 5 ? ' / ' + levels[level] : ''),
    activity: '**Активность** · ' + member + '\nСообщений: ' + (item?.messages || 0)
      + '\nМинут в голосовых: ' + Math.floor((item?.voice_ms || 0) / 60_000)
      + '\nПоследняя активность: ' + (item ? '<t:' + Math.floor(item.last / 1000) + ':R>' : 'ещё не записана'),
  };
}
export function leaderboard(guild) {
  const items = db.prepare('SELECT user,total FROM xp WHERE guild=? ORDER BY total DESC, user').all(guild.id)
    .filter(item => guild.members.cache.has(item.user)).slice(0, 10);
  return items.map((item, index) => (index + 1) + '. <@' + item.user + '> · ' + item.total + ' XP').join('\n') || 'Таблица пока пуста.';
}

export async function inactivity(guild, enforce = false) {
  const config = settings(guild.id);
  if (!config.ready) return [];
  const now = Date.now();
  const candidates = [];
  for (const member of (await guild.members.fetch()).values()) {
    if (!enrolled(member, config)) continue;
    seed.run(guild.id, member.id, now);
    const item = getActivity.get(guild.id, member.id);
    if (member.id === guild.ownerId || admin(member) || has(member, ['boss'], config)) continue;
    if (now - Math.max(item.last, member.joinedTimestamp || 0) < cutoff || eligibleVoice(member.voice)) continue;
    candidates.push(member);
    if (!enforce) continue;
    if (!member.kickable) {
      await log(guild, 'Не удалось исключить за неактив', member + ' · проверь иерархию ролей', true);
      continue;
    }
    const latest = getActivity.get(guild.id, member.id);
    if (now - latest.last < cutoff || eligibleVoice(member.voice)) continue;
    try {
      await member.kick('Black Squad · 30 дней без активности');
      await log(guild, 'Исключён за неактив', member + ' · последнее действие <t:' + Math.floor(latest.last / 1000) + ':R>', true);
    } catch (error) {
      await log(guild, 'Ошибка исключения за неактив', member + ' · ' + error.message, true);
    }
  }
  return candidates;
}

export async function voiceTick(client) {
  expireDrafts();
  const now = Date.now();
  for (const [key, at] of cooldowns) if (now - at > 60_000) cooldowns.delete(key);
  const connected = new Set();
  for (const guild of client.guilds.cache.values()) {
    for (const state of guild.voiceStates.cache.values()) {
      if (!eligibleVoice(state)) continue;
      const { member } = state;
      const key = keyOf(member);
      connected.add(key);
      const session = sessions.get(key);
      if (!session) {
        record.run(guild.id, member.id, now, 0, 0);
        sessions.set(key, { last: now, remainder: 0 });
        continue;
      }
      const xp = flushVoice(member, session, now);
      if (xp) await award(member, xp).catch(error => console.error('Голосовой XP', member.id, error));
    }
  }
  for (const key of sessions.keys()) if (!connected.has(key)) sessions.delete(key);
}
