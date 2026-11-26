import {
  AttachmentBuilder, Client, Events, GatewayIntentBits as I, Partials, PermissionFlagsBits as P, SlashCommandBuilder,
} from 'discord.js';
import { admin, assign, audit, channelButtons, channelSelect, has, log, reason, setup } from './server.js';
import { onboarding, privateReply, restoreApplications, review } from './recruitment.js';
import { describe, inactivity, leaderboard, messageActivity, resetJoin, syncStars, trackMember, voiceActivity, voiceTick } from './activity.js';
import { clan, reviewers } from './settings.js';
import { db, settings } from './store.js';
import { listen } from './logging.js';
import { cleanRooms, roomAction, roomVoice } from './rooms.js';

const token = process.env.DISCORD_TOKEN;
const guildId = process.env.DISCORD_GUILD_ID;
if (!token || !/^\d{17,20}$/u.test(guildId || '')) throw new Error('Заполни DISCORD_TOKEN и DISCORD_GUILD_ID в .env.');
const client = new Client({
  intents: [I.Guilds, I.GuildMembers, I.GuildMessages, I.MessageContent, I.GuildVoiceStates, I.GuildModeration],
  partials: [Partials.Message, Partials.Channel],
  allowedMentions: { parse: [], repliedUser: false },
});
const enabled = guild => guild?.id === guildId;
const handle = promise => promise.catch(error => console.error(error));
const adminCommands = ['setup', 'admin', 'server_audit', 'inactive'];
const command = (name, description, user = false) => {
  const result = new SlashCommandBuilder().setName(name).setDescription(description);
  if (adminCommands.includes(name)) result.setDefaultMemberPermissions(P.Administrator);
  if (user) result.addUserOption(option => option.setName('member').setDescription('Участник клана'));
  return result.toJSON();
};
const commands = [
  command('setup', 'Создать или обновить структуру Black Squad'),
  command('admin', 'Назначить каналы Black Squad через кнопки'),
  command('server_audit', 'Проверить роли, каналы, права и панели Black Squad'),
  command('rank', 'Посмотреть звёзды и XP', true),
  command('top', 'Посмотреть топ участников по XP'),
  command('activity', 'Посмотреть активность участника', true),
  command('inactive', 'Посмотреть кандидатов на исключение за 30 дней неактива'),
  new SlashCommandBuilder().setName('promote').setDescription('Перевести пробника в основной состав')
    .addUserOption(option => option.setName('member').setDescription('Пробник').setRequired(true)).toJSON(),
];
const operations = new Set();
async function configure(inter, fn) {
  if (operations.has(inter.guildId)) return privateReply(inter, 'Настройка уже выполняется.');
  operations.add(inter.guildId);
  try {
    await inter.deferReply({ flags: 64 });
    await fn();
  } finally {
    operations.delete(inter.guildId);
  }
}

async function dispatch(inter) {
  if (!inter.inCachedGuild() || !enabled(inter.guild)) return;
  const guild = inter.guild;
  const config = settings(guild.id);
  if (inter.isChatInputCommand()) {
    const name = inter.commandName;
    if (adminCommands.includes(name) && !admin(inter.member)) return privateReply(inter, 'Команда доступна администрации.');
    if (name === 'setup') {
      return configure(inter, async () => {
        if (guild.members.me.nickname !== 'Black Bot') await guild.members.me.setNickname('Black Bot', reason);
        await setup(guild);
        for (const member of (await guild.members.fetch()).values()) trackMember(member);
        await inter.editReply('Структура Black Squad готова. /admin назначает каналы, /server_audit проверяет сервер.');
      });
    }
    if (name === 'admin') return inter.reply({ content: 'Выбери назначение канала.', flags: 64, components: channelButtons() });
    if (name === 'server_audit' || name === 'inactive') {
      await inter.deferReply({ flags: 64 });
      const findings = name === 'server_audit' ? await audit(guild) :
        (await inactivity(guild)).map(member => member.user.username + ' (' + member.id + ')');
      const heading = name === 'server_audit' ? 'Аудит Black Squad' : 'Неактив 30 дней';
      if (!findings.length) return inter.editReply(heading + ' · замечаний нет.');
      const output = findings.join('\n');
      if (output.length < 1850) return inter.editReply(heading + '\n' + output);
      return inter.editReply({ content: heading, files: [new AttachmentBuilder(Buffer.from(output), { name: name + '.txt' })] });
    }
    if (!config.ready) return privateReply(inter, 'Сначала нужна настройка Black Squad.');
    if (name === 'promote') {
      if (!has(inter.member, reviewers, config) && inter.user.id !== guild.ownerId) return privateReply(inter, 'Перевод в состав доступен штабу.');
      const target = inter.options.getMember('member');
      if (!target || !has(target, ['trial'], config)) return privateReply(inter, 'Выбери пробника.');
      await inter.deferReply({ flags: 64 });
      const roles = [...target.roles.cache.keys()].filter(id => id !== guild.id && id !== config.roles.trial);
      await target.edit({ roles: [...new Set([...roles, config.roles.member])], reason });
      await inter.editReply(target + ' теперь в основном составе.');
      return log(guild, 'Перевод в основной состав', inter.member + ' · ' + target, true);
    }
    if (!has(inter.member, clan, config) && !admin(inter.member)) return privateReply(inter, 'Статистика доступна участникам клана.');
    if (name === 'top') return privateReply(inter, leaderboard(guild));
    const target = inter.options.getMember('member') || inter.member;
    if (name === 'rank' || name === 'activity') return privateReply(inter, describe(target)[name]);
  }
  const parts = inter.customId?.split(':');
  if (!parts || parts[0] !== 'bs') return;
  const [, action, key, id] = parts;
  if (action === 'channel' || action === 'assign') {
    if (!admin(inter.member)) return privateReply(inter, 'Настройка доступна администрации.');
    if (action === 'channel') return inter.reply({ content: 'Выбери канал. Бот применит к нему права выбранного раздела.', components: [channelSelect(key)], flags: 64 });
    return configure(inter, async () => {
      const channel = await guild.channels.fetch(inter.values[0]);
      await assign(guild, key, channel);
      await inter.editReply('Назначен ' + channel);
    });
  }
  if (action === 'room') return roomAction(inter, key);
  if (action === 'review') return review(inter, key, Number(id));
  return onboarding(inter, action);
}

client.on(Events.InteractionCreate, inter => handle(dispatch(inter).catch(async error => {
  console.error('Ошибка действия', inter.commandName || inter.customId, error);
  const content = 'Действие не завершено. ' + error.message.slice(0, 300);
  if (inter.deferred) await inter.editReply(content);
  else if (!inter.replied) await privateReply(inter, content);
})));
listen(client, enabled, handle);
client.on(Events.GuildMemberAdd, member => {
  if (!enabled(member.guild) || member.user.bot) return;
  handle((async () => {
    const config = settings(member.guild.id);
    if (!config.ready) return;
    resetJoin(member);
    await member.roles.add(config.roles.unverified, reason);
    await log(member.guild, 'Участник присоединился', member + ' · ожидает верификацию');
  })());
});
client.on(Events.GuildMemberRemove, member => {
  if (!enabled(member.guild) || member.user.bot) return;
  db.prepare("UPDATE applications SET status='left' WHERE guild=? AND applicant=? AND status IN ('open','claimed')")
    .run(member.guild.id, member.id);
  handle(log(member.guild, 'Участник покинул сервер', member + ' · ' + member.id));
});
client.on(Events.GuildMemberUpdate, (_, member) => {
  if (enabled(member.guild)) trackMember(member);
});
client.on(Events.MessageCreate, message => {
  if (enabled(message.guild)) handle(messageActivity(message));
});
client.on(Events.VoiceStateUpdate, (before, after) => {
  if (!enabled(after.guild)) return;
  handle(voiceActivity(before, after));
  handle(roomVoice(before, after));
});
client.on(Events.Error, console.error);
client.once(Events.ClientReady, () => handle((async () => {
  const guild = await client.guilds.fetch(guildId);
  await guild.commands.set(commands);
  console.log('Black Bot подключён к', guild.name);
  const initial = settings(guild.id);
  if (initial.ready) {
    await cleanRooms(guild);
    await restoreApplications(guild);
    for (const member of (await guild.members.fetch()).values()) {
      trackMember(member);
      if (has(member, clan, initial)) await syncStars(member).catch(error => console.error('Роли XP', member.id, error));
    }
  }
  await voiceTick(client);
  setInterval(() => handle(voiceTick(client)), 60_000);
  const enforce = () => handle(inactivity(guild, true));
  enforce();
  setInterval(enforce, 60 * 60_000);
})()));
await client.login(token);
