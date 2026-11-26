import disnake as d

from server import assign, button
from storage import CHANNELS, get_config, lookup


# Adapted from Hunter Bot's status embed and per-setting channel selector.
def status_embed(guild):
    config = get_config(guild)
    embed = d.Embed(title="Панель управления Black Squad", color=0xa98658)
    for key, (label, *_) in CHANNELS.items():
        channel = lookup(guild, key, "channels", config)
        value = channel.mention if channel else "Канал удалён" if key in config["channels"] else "Не назначен"
        embed.add_field(name=label, value=value, inline=True)
    embed.set_footer(text="Нажми кнопку, чтобы изменить назначение канала")
    return embed


class ChannelSelect(d.ui.ChannelSelect):
    def __init__(self, setting, lock):
        self.setting = setting
        self.lock = lock
        channel_type = d.ChannelType.voice if CHANNELS[setting][2] else d.ChannelType.text
        super().__init__(placeholder="Выбери канал", channel_types=[channel_type])

    async def callback(self, inter):
        if self.lock.locked():
            return await inter.response.send_message("Настройка уже выполняется.", ephemeral=True)
        await inter.response.defer(with_message=False)
        async with self.lock:
            channel_id = self.values[0].id
            channel = inter.guild.get_channel(channel_id) or await inter.guild.fetch_channel(channel_id)
            await assign(inter.guild, self.setting, channel)
        await inter.edit_original_response(content=None, embed=status_embed(inter.guild),
                                           view=AdminPanel(inter.guild, self.lock))


class AdminView(d.ui.View):
    async def interaction_check(self, inter):
        if inter.author.guild_permissions.administrator:
            return True
        await inter.response.send_message("Нужны права администратора.", ephemeral=True)
        return False

    async def on_error(self, error, item, inter):
        import logging
        logging.error("Ошибка панели настройки", exc_info=(type(error), error, error.__traceback__))
        text = "Не удалось назначить канал. " + str(error)[:300]
        if inter.response.is_done():
            await inter.followup.send(text, ephemeral=True)
        else:
            await inter.response.send_message(text, ephemeral=True)


class AdminPanel(AdminView):
    def __init__(self, guild, lock):
        super().__init__(timeout=300)
        for key, (label, *_) in CHANNELS.items():
            style = d.ButtonStyle.success if lookup(guild, key, "channels") else d.ButtonStyle.secondary
            item = button(f"admin-panel:{key}", label, style)
            item.callback = self._select(key, lock)
            self.add_item(item)

    def _select(self, key, lock):
        async def select(inter):
            view = AdminView(timeout=300)
            view.add_item(ChannelSelect(key, lock))

            async def back(action):
                await action.response.edit_message(content=None, embed=status_embed(action.guild),
                                                   view=AdminPanel(action.guild, lock))

            back_button = button("admin-panel:back", "Назад", d.ButtonStyle.secondary)
            back_button.callback = back
            view.add_item(back_button)
            await inter.response.edit_message(content=f"Выбери канал для «{CHANNELS[key][0]}». "
                                                       "Бот применит к нему права раздела.",
                                              embed=None, view=view)
        return select
