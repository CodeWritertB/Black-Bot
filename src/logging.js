import { AuditLogEvent, Events } from 'discord.js';
import { log } from './server.js';

export function listen(client, enabled, handle) {
  const on = (event, listener) => client.on(event, (...args) => {
    const guild = args[0].guild || args[1]?.guild;
    if (guild && enabled(guild)) handle(listener(...args));
  });
  on(Events.MessageDelete, async message => {
    if (message.author?.bot) return;
    await log(message.guild, 'Сообщение удалено', (message.author || 'Автор неизвестен') + ' · <#' + message.channelId + '>\n' +
      (message.content || '(текст недоступен)') + (message.attachments?.size ? '\nВложений: ' + message.attachments.size : ''));
  });
  on(Events.MessageUpdate, async (before, after) => {
    if (after.partial || after.author?.bot || before.content === after.content) return;
    await log(after.guild, 'Сообщение изменено', after.author + ' · ' + after.url +
      '\nБыло: ' + (before.content || '(недоступно)') + '\nСтало: ' + (after.content || '(без текста)'));
  });
  on(Events.MessageBulkDelete, async (messages, channel) => {
    await log(channel.guild, 'Массовое удаление сообщений', channel + ' · удалено: ' + messages.size);
  });
  on(Events.GuildMemberUpdate, async (before, after) => {
    const changes = [];
    if (before.nickname !== after.nickname) changes.push((before.nickname || before.user.username) + ' → ' + (after.nickname || after.user.username));
    const added = after.roles.cache.filter(role => !before.roles.cache.has(role.id));
    const removed = before.roles.cache.filter(role => !after.roles.cache.has(role.id));
    if (added.size) changes.push('Выданы: ' + added.map(role => role.toString()).join(', '));
    if (removed.size) changes.push('Сняты: ' + removed.map(role => role.toString()).join(', '));
    if (changes.length) await log(after.guild, 'Участник обновлён', after + '\n' + changes.join('\n'));
  });
  on(Events.VoiceStateUpdate, async (before, after) => {
    if (before.channelId === after.channelId) return;
    await log(after.guild, 'Голосовой канал', '<@' + after.id + '> · ' + (before.channel || 'вне канала') + ' → ' + (after.channel || 'вне канала'));
  });
  const audited = new Map([
    [AuditLogEvent.ChannelCreate, 'Канал создан'], [AuditLogEvent.ChannelUpdate, 'Канал изменён'],
    [AuditLogEvent.ChannelDelete, 'Канал удалён'], [AuditLogEvent.ChannelOverwriteCreate, 'Доступ к каналу добавлен'],
    [AuditLogEvent.ChannelOverwriteUpdate, 'Доступ к каналу изменён'], [AuditLogEvent.ChannelOverwriteDelete, 'Доступ к каналу удалён'],
    [AuditLogEvent.RoleCreate, 'Роль создана'], [AuditLogEvent.RoleUpdate, 'Роль изменена'], [AuditLogEvent.RoleDelete, 'Роль удалена'],
    [AuditLogEvent.MemberKick, 'Участник исключён'], [AuditLogEvent.MemberBanAdd, 'Участник заблокирован'],
    [AuditLogEvent.MemberBanRemove, 'Блокировка снята'], [AuditLogEvent.MessageDelete, 'Модератор удалил сообщение'],
    [AuditLogEvent.MessageBulkDelete, 'Массовое удаление модератором'],
    [AuditLogEvent.MemberRoleUpdate, 'Модератор изменил роли'], [AuditLogEvent.MemberUpdate, 'Модератор обновил участника'],
  ]);
  client.on(Events.GuildAuditLogEntryCreate, (entry, guild) => {
    if (!enabled(guild) || !audited.has(entry.action)) return;
    handle(log(guild, audited.get(entry.action), 'Исполнитель: ' + (entry.executorId ? '<@' + entry.executorId + '>' : 'не указан')
      + '\nЦель: ' + (entry.target?.name || entry.target?.username || entry.targetId || 'не указана')
      + (entry.reason ? '\nПричина: ' + entry.reason : ''), true));
  });
}
