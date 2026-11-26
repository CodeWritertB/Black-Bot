import {
  ActionRowBuilder, ButtonStyle, LabelBuilder, ModalBuilder, StringSelectMenuBuilder, TextInputBuilder, TextInputStyle,
} from 'discord.js';
import { applicationCard } from './cards.js';
import { clan, fields, reviewers } from './settings.js';
import { db, settings } from './store.js';
import { button, channelFor, has, log, reason, row } from './server.js';

const drafts = new Map();
const busy = new Set();
const read = db.prepare('SELECT * FROM applications WHERE guild=? AND id=?');
export const privateReply = (inter, content) => inter.reply({ content, flags: 64 });
const inputs = (keys, values = {}) => fields.filter(([key]) => keys.includes(key)).map(([key, label, length]) => {
  const input = new TextInputBuilder().setCustomId(key).setLabel(label).setMaxLength(length)
    .setStyle(length > 100 ? TextInputStyle.Paragraph : TextInputStyle.Short);
  if (values[key]) input.setValue(values[key]);
  return new ActionRowBuilder().addComponents(input);
});
const modal = (id, title, elements) => new ModalBuilder().setCustomId(id).setTitle(title).addComponents(elements);
const display = (nick, name) => {
  if (!name || name.length > 27) throw new Error('Имя должно содержать от 1 до 27 символов.');
  return nick.slice(0, 29 - name.length).trimEnd() + ' (' + name + ')';
};
const statusText = app => {
  const reviewer = app.reviewer ? '<@' + app.reviewer + '>' : '';
  const labels = { open: 'Ожидает рекрутёра', claimed: 'На рассмотрении у ' + reviewer,
    accepting: 'Принимается · ' + reviewer, rejecting: 'Отклоняется · ' + reviewer,
    accepted: 'Принята · ' + reviewer, rejected: 'Отклонена · ' + reviewer, left: 'Кандидат покинул сервер' };
  return '**Заявка №' + app.id + '** · <@' + app.applicant + '>\n' + labels[app.status];
};

function reviewPanel(app) {
  const controls = [];
  if (app.status === 'open') controls.push(button('bs:review:claim:' + app.id, 'Взять заявку'));
  if (['claimed', 'accepting'].includes(app.status)) controls.push(button('bs:review:accept:' + app.id, 'Принять', ButtonStyle.Success));
  if (['claimed', 'rejecting'].includes(app.status)) controls.push(button('bs:review:reject:' + app.id, 'Отклонить и кикнуть', ButtonStyle.Danger));
  if (app.status === 'claimed') controls.push(button('bs:review:release:' + app.id, 'Вернуть в очередь', ButtonStyle.Secondary));
  return { content: statusText(app), components: controls.length ? [row(...controls)] : [], allowedMentions: { parse: [] } };
}

export async function onboarding(inter, action) {
  const guild = inter.guild;
  const config = settings(guild.id);
  const member = inter.member;
  if (!config.ready) return privateReply(inter, 'Вход ещё настраивается.');
  if (action.startsWith('verify')) {
    if (inter.channelId !== config.channels.verify) return privateReply(inter, 'Открой действующий канал верификации.');
    if (has(member, ['guest', ...clan], config)) return privateReply(inter, 'Ты уже прошёл верификацию.');
    if (action === 'verify') {
      const input = new TextInputBuilder().setCustomId('nickname').setLabel('Ник (Имя)').setPlaceholder('BlackWolf (Артём)')
        .setStyle(TextInputStyle.Short).setMaxLength(32);
      return inter.showModal(modal('bs:verify-submit', 'Изменить никнейм', [row(input)]));
    }
    const nick = inter.fields.getTextInputValue('nickname').trim();
    if (!/^\S.*\s\([^()]+\)$/u.test(nick)) return privateReply(inter, 'Введи ник в формате **Ник (Имя)**.');
    await inter.deferReply({ flags: 64 });
    const roles = member.roles.cache.filter(role => role.id !== guild.id && role.id !== config.roles.unverified);
    await member.edit({ nick, roles: [...roles.keys(), config.roles.guest], reason });
    await inter.editReply('Верификация пройдена. Теперь доступны правила, описание клана и заявка.');
    return log(guild, 'Верификация', member.toString());
  }
  if (inter.channelId !== config.channels.apply || !has(member, ['guest'], config) || has(member, clan, config)) {
    return privateReply(inter, 'Подача заявки доступна верифицированным кандидатам в канале набора.');
  }
  const key = guild.id + ':' + member.id;
  const active = db.prepare("SELECT id FROM applications WHERE guild=? AND applicant=? AND status IN ('publishing','open','claimed','accepting','rejecting')").get(guild.id, member.id);
  if (active) return privateReply(inter, 'Заявка №' + active.id + ' уже на рассмотрении.');
  const shortKeys = ['identity', 'nick', 'hours', 'activity'];
  if (drafts.get(key)?.expires < Date.now()) drafts.delete(key);
  if (action === 'apply' || action === 'apply-edit') {
    const selection = new StringSelectMenuBuilder().setCustomId('specialty').setMinValues(1).setMaxValues(2)
      .addOptions({ label: 'PvP', value: 'pvp' }, { label: 'PvE', value: 'pve' });
    const elements = inputs(shortKeys, drafts.get(key)?.data).map(item => {
      const input = item.components[0];
      const label = input.data.label;
      delete input.data.label;
      return new LabelBuilder().setLabel(label).setTextInputComponent(input);
    });
    elements.push(new LabelBuilder().setLabel('Специализация').setStringSelectMenuComponent(selection));
    return inter.showModal(modal('bs:apply-part1', 'Заявка в Black Squad · 1 из 2', elements));
  }
  if (action === 'apply-part1') {
    const data = Object.fromEntries(shortKeys.map(field => [field, inter.fields.getTextInputValue(field).trim()]));
    if (Object.values(data).some(value => !value) || !/^\d{1,7}$/u.test(data.hours)) {
      return inter.reply({ content: 'Заполни все поля. Часы укажи целым числом.', flags: 64,
        components: [row(button('bs:apply-edit', 'Исправить'))] });
    }
    const match = /^(.*?)\s*[, ]\s*(\d{1,3})(?:\s+(?:лет|года|год))?$/u.exec(data.identity);
    if (!match?.[1]?.trim() || match[1].trim().length > 27 || Number(match[2]) < 1 || Number(match[2]) > 120) {
      return inter.reply({ content: 'Введи имя и возраст, например **Артём, 21**.', flags: 64,
        components: [row(button('bs:apply-edit', 'Исправить'))] });
    }
    data.name = match[1].trim();
    data.nick = data.nick.replace(/\s+/gu, ' ');
    data.specialtyKeys = inter.fields.getStringSelectValues('specialty');
    data.specialty = data.specialtyKeys.map(value => value === 'pvp' ? 'PvP' : 'PvE').join(' / ');
    drafts.set(key, { data, expires: Date.now() + 30 * 60_000 });
    return inter.reply({ content: 'Основные данные сохранены. Остались два поля.', flags: 64,
      components: [row(button('bs:apply-next', 'Продолжить'), button('bs:apply-edit', 'Изменить ответы', ButtonStyle.Secondary))] });
  }
  const draft = drafts.get(key);
  if (!draft || draft.expires < Date.now()) return privateReply(inter, 'Черновик истёк. Начни заявку заново.');
  if (action === 'apply-next') return inter.showModal(modal('bs:apply-submit', 'Заявка в Black Squad · 2 из 2', inputs(['previous', 'expectations'])));
  if (action !== 'apply-submit') return;
  const data = { ...draft.data };
  for (const field of ['previous', 'expectations']) data[field] = inter.fields.getTextInputValue(field).trim();
  if (!data.previous || !data.expectations) return inter.reply({ content: 'Заполни оба поля. Если кланов не было, напиши «Не состоял».', flags: 64,
    components: [row(button('bs:apply-next', 'Исправить'))] });
  const channel = channelFor(guild, 'reviews');
  if (!channel) return privateReply(inter, 'Канал рассмотрения не настроен. Сообщи рекрутёру.');
  await inter.deferReply({ flags: 64 });
  const entry = db.prepare('INSERT OR IGNORE INTO applications(guild,applicant,data,channel) VALUES (?,?,?,?)')
    .run(guild.id, member.id, JSON.stringify(data), channel.id);
  if (!entry.changes) return inter.editReply('У тебя уже есть активная заявка.');
  const id = entry.lastInsertRowid;
  const app = { id, applicant: member.id, status: 'open' };
  let message;
  try {
    message = await channel.send({ ...reviewPanel(app), components: [],
      files: [{ attachment: applicationCard(id, inter.user.username, data), name: 'black-squad-' + id + '.png' }] });
  } catch (error) {
    db.prepare("DELETE FROM applications WHERE id=? AND status='publishing'").run(id);
    throw error;
  }
  db.prepare("UPDATE applications SET status='open',message=? WHERE id=?").run(message.id, id);
  drafts.delete(key);
  await message.edit(reviewPanel(app)).catch(error => console.error('Панель заявки', id, error));
  await inter.editReply('Заявка №' + id + ' отправлена. Ожидай ответа рекрутёра.');
  await log(guild, 'Новая заявка', '#' + id + ' · ' + member, true);
}

export async function review(inter, action, id) {
  const guild = inter.guild;
  const config = settings(guild.id);
  if (!has(inter.member, reviewers, config) && inter.user.id !== guild.ownerId) return privateReply(inter, 'Заявки рассматривает штаб.');
  let app = read.get(guild.id, id);
  if (!app || app.channel !== inter.channelId || app.message !== inter.message.id) return privateReply(inter, 'Кнопка относится к другой заявке.');
  await inter.deferReply({ flags: 64 });
  if (busy.has(id)) return inter.editReply('Заявку уже обрабатывают.');
  busy.add(id);
  try {
    app = read.get(guild.id, id);
    if (action === 'claim') {
      const updated = db.prepare("UPDATE applications SET status='claimed',reviewer=? WHERE id=? AND status='open'").run(inter.user.id, id);
      if (!updated.changes) return inter.editReply('Эту заявку уже взяли или закрыли.');
    } else {
      if (app.reviewer !== inter.user.id) return inter.editReply('Решение принимает тот, кто взял заявку.');
      if (action === 'release') {
        if (app.status !== 'claimed') return inter.editReply('Заявку нельзя вернуть в очередь.');
        db.prepare("UPDATE applications SET status='open',reviewer=NULL WHERE id=?").run(id);
      } else {
        const accepting = action === 'accept';
        const processing = accepting ? 'accepting' : 'rejecting';
        if (!['claimed', processing].includes(app.status)) return inter.editReply('По заявке уже есть решение.');
        db.prepare('UPDATE applications SET status=? WHERE id=?').run(processing, id);
        const candidate = await guild.members.fetch({ user: app.applicant, force: true }).catch(error => {
          if (error.code !== 10007) throw error;
          return null;
        });
        let result = candidate ? (accepting ? 'accepted' : 'rejected') : accepting ? 'left' : 'rejected';
        if (candidate && accepting) {
          const data = JSON.parse(app.data);
          const remove = ['unverified', 'guest', 'pvp', 'pve'].map(key => config.roles[key]);
          const keep = [...candidate.roles.cache.keys()].filter(role => role !== guild.id && !remove.includes(role));
          const add = ['trial', ...data.specialtyKeys].map(key => config.roles[key]);
          if (add.some(role => !guild.roles.cache.get(role)?.editable)) throw new Error('Нужные роли отсутствуют или находятся выше Black Bot.');
          await candidate.edit({ nick: display(data.nick, data.name), roles: [...new Set([...keep, ...add])], reason: 'Принята заявка №' + id });
          db.prepare(`INSERT INTO activity(guild,user,last) VALUES (?,?,?)
            ON CONFLICT(guild,user) DO UPDATE SET last=excluded.last`).run(guild.id, candidate.id, Date.now());
        }
        if (candidate && !accepting) await candidate.kick('Отклонена заявка Black Squad №' + id);
        db.prepare('UPDATE applications SET status=? WHERE id=?').run(result, id);
        await log(guild, 'Решение по заявке', statusText(read.get(guild.id, id)), true);
      }
    }
    await inter.editReply(statusText(read.get(guild.id, id)));
  } finally {
    busy.delete(id);
    await inter.message.edit(reviewPanel(read.get(guild.id, id))).catch(error => console.error('Обновление заявки', id, error));
  }
}

export async function restoreApplications(guild) {
  const pending = db.prepare("SELECT * FROM applications WHERE guild=? AND status IN ('publishing','open','claimed','accepting','rejecting')").all(guild.id);
  for (const app of pending) {
    const channel = guild.channels.cache.get(app.channel);
    if (!channel) continue;
    try {
      if (app.status === 'publishing') {
        const matches = await channel.messages.fetch({ limit: 50 });
        const message = matches.find(item => item.author.id === guild.members.me.id && item.attachments.some(file => file.name === 'black-squad-' + app.id + '.png'));
        if (!message) {
          db.prepare("DELETE FROM applications WHERE id=? AND status='publishing'").run(app.id);
          continue;
        }
        db.prepare("UPDATE applications SET status='open',message=? WHERE id=?").run(message.id, app.id);
        await message.edit(reviewPanel(read.get(guild.id, app.id)));
      } else {
        const message = await channel.messages.fetch(app.message);
        await message.edit(reviewPanel(app));
      }
    } catch (error) {
      if (error.code === 10008 && ['open', 'claimed'].includes(app.status)) db.prepare("UPDATE applications SET status='left' WHERE id=?").run(app.id);
      else console.error('Восстановление заявки', app.id, error);
    }
  }
}
export function expireDrafts() {
  for (const [key, draft] of drafts) if (draft.expires < Date.now()) drafts.delete(key);
}
