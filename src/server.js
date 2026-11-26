import { ActionRowBuilder, ButtonBuilder, ButtonStyle, ChannelType, ChannelSelectMenuBuilder, PermissionFlagsBits as P } from 'discord.js';
import { categories, channels, roles, clan, reviewers, leadership } from './settings.js';
import { db, save, settings } from './store.js';

export const reason = 'Black Bot · Black Squad';
export const button = (id, label, style = ButtonStyle.Primary) => new ButtonBuilder().setCustomId(id).setLabel(label).setStyle(style);
export const row = (...items) => new ActionRowBuilder().addComponents(items);
export const has = (member, keys, config = settings(member.guild.id)) => keys.some(key => member.roles.cache.has(config.roles[key]));
export const admin = member => member.permissions.has(P.Administrator);
export const channelFor = (guild, key) => guild.channels.cache.get(settings(guild.id).channels[key]);
const required = ['ManageRoles', 'ManageChannels', 'ManageNicknames', 'ChangeNickname', 'KickMembers', 'ViewAuditLog', 'MoveMembers', 'Connect', 'ViewChannel', 'SendMessages', 'ReadMessageHistory', 'EmbedLinks', 'AttachFiles'];

export function overwrites(guild, config, key) {
  const [, , type, audience, write] = channels[key];
  const open = key === 'verify';
  const everyone = open ? { allow: [P.ViewChannel, P.ReadMessageHistory], deny: [P.SendMessages, P.SendMessagesInThreads] } : { deny: [P.ViewChannel] };
  return [
    { id: guild.id, ...everyone },
    { id: guild.members.me.id, type: 1, allow: [P.ViewChannel, P.SendMessages, P.ReadMessageHistory, P.AttachFiles, P.EmbedLinks] },
    ...(open ? [] : [...new Set(audience)].map(role => ({
      id: config.roles[role],
      allow: [P.ViewChannel, P.ReadMessageHistory, P.UseApplicationCommands, ...(write ? type === ChannelType.GuildVoice ? [P.Connect, P.Speak, P.SendMessages] : [P.SendMessages] : [])],
      deny: write ? [] : [P.SendMessages, P.SendMessagesInThreads, P.CreatePublicThreads, P.CreatePrivateThreads],
    }))),
  ];
}

const panels = {
  verify: ['**Верификация Black Squad**\nНажми кнопку и введи ник в формате **Ник (Имя)**, например `BlackWolf (Артём)`.', 'verify', 'Изменить никнейм'],
  rules: ['**Правила Black Squad**\n1. Уважай участников. Травля, угрозы и дискриминация запрещены.\n2. Без спама, мошенничества, читов и публикации чужих данных.\n3. Соблюдай договорённости отряда и решай конфликты через штаб.\n4. Через 30 дней без сообщений и голосовой активности бот исключает участников клана.', null],
  info: ['**BLACK SQUAD · ROB RUN**\nКлан для ПВП, рейдов и ПВЕ-фарма. В штабе координируют бои за бизнес-территории и развитие клана в течение вайпа. После собеседования новички начинают с испытательного срока.', null],
  apply: ['**Вступить в BLACK SQUAD**\nЗаполни заявку в два шага. Выбери ПВП, ПВЕ или обе специализации. Для собеседования зайди в одноимённый голосовой канал.', 'apply', 'Заполнить заявку'],
};

export async function publish(guild, config, key) {
  if (!panels[key]) return;
  const [content, id, label] = panels[key];
  const channel = guild.channels.cache.get(config.channels[key]);
  const payload = { content, components: id ? [row(button('bs:' + id, label))] : [] };
  const panel = config.panels[key];
  const oldChannel = panel && guild.channels.cache.get(panel.channel);
  const old = await oldChannel?.messages.fetch(panel.message).catch(error => {
    if (error.code !== 10008) throw error;
  });
  if (old && old.channelId === channel.id && old.author.id === guild.members.me.id) {
    await old.edit(payload);
  } else {
    const message = await channel.send(payload);
    config.panels[key] = { channel: channel.id, message: message.id };
    save(guild.id, config);
    if (old?.author.id === guild.members.me.id) await old.edit({ components: [] });
  }
}

export async function setup(guild) {
  const config = settings(guild.id);
  const me = await guild.members.fetchMe();
  const missing = required.filter(name => !me.permissions.has(P[name]));
  if (missing.length) throw new Error('Права бота: ' + missing.join(', '));
  await guild.channels.fetch();
  await guild.roles.fetch();
  for (const [key, name] of Object.entries(roles)) {
    let role = guild.roles.cache.get(config.roles[key]) || guild.roles.cache.find(item => !item.managed && item.name === name);
    if (!role) role = await guild.roles.create({ name, reason });
    if (!role.editable) throw new Error('Подними роль Black Bot выше роли «' + name + '».');
    config.roles[key] = role.id;
    save(guild.id, config);
  }
  let botLabel = guild.roles.cache.get(config.botRole) || guild.roles.cache.find(item => !item.managed && item.name === 'Бот' && item.permissions.bitfield === 0n);
  if (!botLabel) botLabel = await guild.roles.create({ name: 'Бот', permissions: [], reason });
  if (!me.roles.cache.has(botLabel.id)) await me.roles.add(botLabel, reason);
  config.botRole = botLabel.id;
  save(guild.id, config);
  for (const [key, name] of Object.entries(categories)) {
    let category = guild.channels.cache.get(config.categories[key]);
    if (!category) category = await guild.channels.create({
      name, type: ChannelType.GuildCategory, reason,
      permissionOverwrites: [{ id: guild.id, deny: [P.ViewChannel] }, { id: me.id, type: 1, allow: [P.ViewChannel] }],
    });
    config.categories[key] = category.id;
    save(guild.id, config);
  }
  for (const [key, [name, category, type]] of Object.entries(channels)) {
    let channel = guild.channels.cache.get(config.channels[key]);
    if (channel) await channel.permissionOverwrites.set(overwrites(guild, config, key), reason);
    else channel = await guild.channels.create({ name, type, parent: config.categories[category], reason, permissionOverwrites: overwrites(guild, config, key) });
    config.channels[key] = channel.id;
    save(guild.id, config);
    await publish(guild, config, key);
  }
  config.ready = true;
  save(guild.id, config);
}

export function channelButtons() {
  const buttons = Object.entries(channels).map(([key, [name]]) => button('bs:channel:' + key, name, ButtonStyle.Secondary));
  return [row(...buttons.slice(0, 5)), row(...buttons.slice(5, 10)), row(...buttons.slice(10))];
}

export function channelSelect(key) {
  return row(new ChannelSelectMenuBuilder().setCustomId('bs:assign:' + key)
    .setPlaceholder(channels[key][0]).setChannelTypes(channels[key][2]));
}

export async function assign(guild, key, channel) {
  const config = settings(guild.id);
  if (!config.ready) throw new Error('Сначала выполни /setup.');
  if (channel.type !== channels[key][2]) throw new Error('Канал неверного типа.');
  if (Object.entries(config.channels).some(([other, id]) => other !== key && id === channel.id)) throw new Error('Этот канал уже назначен.');
  if (key === 'reviews' && config.channels[key] !== channel.id &&
    db.prepare("SELECT 1 FROM applications WHERE guild=? AND status IN ('publishing','open','claimed','accepting','rejecting') LIMIT 1").get(guild.id)) {
    throw new Error('Перед переносом канала закрой активные заявки.');
  }
  await channel.permissionOverwrites.set(overwrites(guild, config, key), reason);
  config.channels[key] = channel.id;
  save(guild.id, config);
  await publish(guild, config, key);
}

export async function audit(guild) {
  const config = settings(guild.id);
  await guild.channels.fetch();
  await guild.roles.fetch();
  await guild.members.fetchMe();
  const issues = [];
  if (!config.ready) issues.push('Настройка /setup ещё не завершена.');
  const missing = required.filter(name => !guild.members.me.permissions.has(P[name]));
  if (missing.length) issues.push('Права бота: ' + missing.join(', '));
  for (const [key, name] of Object.entries(roles)) {
    const role = guild.roles.cache.get(config.roles[key]);
    if (!role) issues.push('Роль отсутствует: ' + name);
    else if (!role.editable) issues.push('Роль вне доступа бота: ' + name);
    if (role?.permissions.has(P.Administrator) && !['boss', 'coder'].includes(key)) issues.push('Проверь право Administrator: ' + name);
  }
  for (const [key, [name, , type]] of Object.entries(channels)) {
    const channel = guild.channels.cache.get(config.channels[key]);
    if (!channel || channel.type !== type) { issues.push('Проверь канал: ' + name); continue; }
    if (config.ready && Object.values(config.roles).every(id => guild.roles.cache.has(id))) {
      const expected = overwrites(guild, config, key);
      const actual = channel.permissionOverwrites.cache;
      const mismatch = expected.some(entry => {
        const overwrite = actual.get(entry.id);
        return !overwrite || (entry.allow || []).some(flag => !overwrite.allow.has(flag))
          || (entry.deny || []).some(flag => !overwrite.deny.has(flag));
      });
      if (mismatch || actual.some(entry => entry.allow.has(P.ViewChannel) && !expected.some(item => item.id === entry.id))) {
        issues.push('Проверь доступ: ' + channel.toString());
      }
    }
    if (panels[key]) {
      const panel = config.panels[key];
      const message = panel?.channel === channel.id && await channel.messages.fetch(panel.message).catch(() => null);
      if (!message || message.author.id !== guild.members.me.id || (panels[key][1] && !message.components.length)) issues.push('Панель отсутствует: ' + name);
    }
  }
  const unmanaged = guild.channels.cache.filter(channel => channel.type !== ChannelType.GuildCategory && !Object.values(config.channels).includes(channel.id)
    && !db.prepare('SELECT 1 FROM rooms WHERE guild=? AND id=?').get(guild.id, channel.id));
  if (unmanaged.size) issues.push('Каналы вне настройки: ' + unmanaged.map(channel => channel.toString()).join(', '));
  for (const member of await guild.members.fetch().then(cache => cache.values())) {
    if (member.user.bot) continue;
    if (!has(member, ['unverified', 'guest', ...clan], config)) issues.push('Без роли доступа: ' + member.toString());
    const posts = leadership.filter(key => has(member, [key], config));
    if (has(member, ['deputy'], config) ? posts.length !== 1 : posts.length > 0) issues.push('Проверь роль «Зам» и должность: ' + member.toString());
  }
  return issues;
}

export async function log(guild, title, description, administrative = false) {
  try {
    await channelFor(guild, administrative ? 'adminLogs' : 'logs')?.send({
      embeds: [{ title, description: description.slice(0, 4096), color: administrative ? 0xa98658 : 0x474e5b, timestamp: new Date().toISOString() }],
    });
  } catch (error) {
    console.error('Ошибка журнала', guild.id, title, error);
  }
}
