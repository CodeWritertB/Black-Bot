import { ChannelType, ModalBuilder, PermissionFlagsBits as P, TextInputBuilder, TextInputStyle } from 'discord.js';
import { clan } from './settings.js';
import { db, settings } from './store.js';
import { button, has, reason, row } from './server.js';

db.exec('CREATE TABLE IF NOT EXISTS rooms (id TEXT PRIMARY KEY, guild TEXT NOT NULL, owner TEXT NOT NULL, locked INTEGER NOT NULL DEFAULT 0)');
const lookup = db.prepare('SELECT * FROM rooms WHERE guild=? AND id=?');
const creating = new Set();

export async function cleanRoom(channel) {
  if (!channel || !lookup.get(channel.guild.id, channel.id)) return;
  if (channel.members.size) return;
  await channel.delete(reason).catch(error => { if (error.code !== 10003) throw error; });
  db.prepare('DELETE FROM rooms WHERE id=?').run(channel.id);
}
export async function cleanRooms(guild) {
  for (const { id } of db.prepare('SELECT id FROM rooms WHERE guild=?').all(guild.id)) {
    const channel = guild.channels.cache.get(id);
    if (channel) await cleanRoom(channel);
    else db.prepare('DELETE FROM rooms WHERE id=?').run(id);
  }
}
export async function roomVoice(before, after) {
  if (before.channelId === after.channelId) return;
  await cleanRoom(before.channel);
  const config = settings(after.guild.id);
  const member = after.member;
  if (!config.ready || !member || member.user.bot || after.channelId !== config.channels.lobby || !has(member, clan, config)) return;
  const key = after.guild.id + ':' + member.id;
  if (creating.has(key)) return;
  creating.add(key);
  let channel;
  try {
    const everyone = { id: after.guild.id, deny: [P.ViewChannel, P.Connect] };
    const audience = [...new Set(clan.map(key => config.roles[key]).filter(Boolean))].map(id => ({
      id, allow: [P.ViewChannel, P.Connect, P.Speak, P.SendMessages, P.ReadMessageHistory],
    }));
    channel = await after.guild.channels.create({
      name: ('Комната ' + member.displayName).slice(0, 100), type: ChannelType.GuildVoice,
      parent: after.channel.parentId, permissionOverwrites: [
        everyone, ...audience,
        { id: after.guild.members.me.id, type: 1, allow: [P.ViewChannel, P.Connect, P.SendMessages, P.ReadMessageHistory] },
        { id: member.id, type: 1, allow: [P.ViewChannel, P.Connect] },
      ], reason,
    });
    db.prepare('INSERT INTO rooms(id,guild,owner) VALUES (?,?,?)').run(channel.id, after.guild.id, member.id);
    if (member.voice.channelId !== config.channels.lobby) return await cleanRoom(channel);
    await member.voice.setChannel(channel, reason);
    await channel.send({
      content: '**Управление временной комнатой**',
      components: [row(button('bs:room:name', 'Название'), button('bs:room:limit', 'Лимит'), button('bs:room:lock', 'Закрыть / открыть вход'))],
    });
  } catch (error) {
    await cleanRoom(channel);
    throw error;
  } finally {
    creating.delete(key);
  }
}

export async function roomAction(inter, action) {
  const room = lookup.get(inter.guildId, inter.channelId);
  if (!room || room.owner !== inter.user.id) return inter.reply({ content: 'Управление доступно владельцу комнаты.', flags: 64 });
  const channel = inter.channel;
  if (action === 'name' || action === 'limit') {
    const isName = action === 'name';
    const input = new TextInputBuilder().setCustomId('value').setLabel(isName ? 'Новое название' : 'Лимит от 0 до 99 (0 — без лимита)')
      .setStyle(TextInputStyle.Short).setMaxLength(isName ? 100 : 2);
    return inter.showModal(new ModalBuilder().setCustomId('bs:room:set-' + action).setTitle('Настройки комнаты').addComponents(row(input)));
  }
  await inter.deferReply({ flags: 64 });
  if (action === 'set-name') {
    const name = inter.fields.getTextInputValue('value').trim();
    if (!name) return inter.editReply('Укажи название.');
    await channel.setName(name, reason);
  } else if (action === 'set-limit') {
    const value = inter.fields.getTextInputValue('value').trim();
    if (!/^\d{1,2}$/u.test(value)) return inter.editReply('Укажи число от 0 до 99.');
    await channel.setUserLimit(Number(value), reason);
  } else if (action === 'lock') {
    const config = settings(inter.guildId);
    const entries = channel.permissionOverwrites.cache.map(overwrite => {
      if (!clan.some(key => config.roles[key] === overwrite.id)) return overwrite;
      const allow = overwrite.allow.toArray().filter(flag => flag !== 'Connect');
      const deny = overwrite.deny.toArray().filter(flag => flag !== 'Connect');
      (room.locked ? allow : deny).push('Connect');
      return { id: overwrite.id, type: overwrite.type, allow, deny };
    });
    await channel.permissionOverwrites.set(entries, reason);
    db.prepare('UPDATE rooms SET locked=? WHERE id=?').run(room.locked ? 0 : 1, room.id);
    return inter.editReply(room.locked ? 'Вход открыт.' : 'Вход закрыт.');
  }
  await inter.editReply('Настройки комнаты обновлены.');
}
