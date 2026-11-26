import { ChannelType } from 'discord.js';

export const roles = {
  boss: 'Босс', deputy: 'Зам', coder: 'Кодер', smuggler: 'Контрабандист',
  business: 'Бизнесмен', accountant: 'Бугалтер', raider: 'Рейдер',
  recruiter: 'Рекрутёр', pvp: 'ПВП', pve: 'ПВЕ', member: 'Свояк',
  trial: 'Пробник', guest: 'Кто ты, воин?', unverified: 'Не верифицирован',
  ...Object.fromEntries([1, 2, 3, 4, 5].map(n => ['star' + n, '⭐'.repeat(n)])),
};
export const leadership = ['coder', 'smuggler', 'business', 'accountant', 'raider'];
export const reviewers = ['boss', 'deputy', 'recruiter', ...leadership];
export const clan = ['trial', 'member', ...reviewers];
export const verified = ['guest', ...clan];
export const levels = [100, 400, 900, 1600, 2500];
export const categories = {
  gate: 'ВХОД', entry: 'BLACK SQUAD · НАБОР', squad: 'BLACK SQUAD · КЛАН', staff: 'BLACK SQUAD · ШТАБ',
};
const text = ChannelType.GuildText;
const voice = ChannelType.GuildVoice;
export const channels = {
  verify: ['верификация', 'gate', text, [], false],
  rules: ['правила', 'entry', text, verified, false],
  info: ['о-клане', 'entry', text, verified, false],
  apply: ['подать-заявку', 'entry', text, verified, false],
  interview: ['Собеседование', 'entry', voice, verified, true],
  chat: ['общий', 'squad', text, clan, true],
  pvp: ['пвп', 'squad', text, ['pvp', ...reviewers], true],
  pve: ['пве', 'squad', text, ['pve', ...reviewers], true],
  voice: ['Штаб Black Squad', 'squad', voice, clan, true],
  lobby: ['Создать комнату', 'squad', voice, clan, true],
  reviews: ['заявки', 'staff', text, reviewers, false],
  logs: ['логи-сервера', 'staff', text, ['boss', 'deputy', 'coder'], false],
  adminLogs: ['админ-логи', 'staff', text, ['boss', 'deputy', 'coder'], false],
};
export const fields = [
  ['identity', 'Имя и возраст', 64],
  ['nick', 'Игровой никнейм', 32],
  ['hours', 'Количество часов в ROB RUN', 10],
  ['activity', 'Время игровой активности в сутки', 100],
  ['specialty', 'Специализация', 20],
  ['previous', 'Предыдущие кланы', 300],
  ['expectations', 'Какие ожидания от BLACK SQUAD', 600],
];
