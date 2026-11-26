import re
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from storage import FIELDS

ASSETS = Path(__file__).with_name("assets")
FONT = ASSETS / "NotoSans-Regular.ttf"


def application_card(number, username, data):
    regular = ImageFont.truetype(str(FONT), 23)
    label_font = ImageFont.truetype(str(FONT), 20)
    title = ImageFont.truetype(str(FONT), 38)
    rows = []
    for key, (label, _, emoji) in FIELDS.items():
        lines, line = [], ""
        for char in " ".join((data.get(key) or "—").split()):
            if regular.getlength(line + char) > 780:
                lines.append(line.rstrip())
                line = ""
            line += char
        lines.append(line.rstrip())
        rows.append((label, emoji, lines))
    image = Image.new("RGB", (920, 190 + sum(78 + 32 * len(lines) for _, _, lines in rows)), "#111318")
    draw = ImageDraw.Draw(image)
    draw.rectangle((48, 42, 100, 46), fill="#c4a675")
    draw.text((48, 57), "BLACK SQUAD", font=title, fill="#dfbd84")
    draw.text((48, 113), f"Заявка №{number}  /  {username}", font=label_font, fill="#c7cbd2")
    y = 170
    for index, (label, emoji, lines) in enumerate(rows, 1):
        height = 78 + len(lines) * 32
        draw.rectangle((48, y, 872, y + height - 14), fill="#1b1e25")
        asset = ASSETS / "application-emoji" / f"{ord(emoji):x}.png"
        with Image.open(asset) as icon:
            badge = icon.convert("RGBA").resize((26, 26), Image.Resampling.LANCZOS)
            image.paste(badge, (68, y + 10), badge)
        draw.text((105, y + 10), f"{index:02}   {label}", font=label_font, fill="#dfbd84")
        for index_line, line in enumerate(lines):
            draw.text((68, y + 44 + index_line * 32), line, font=regular, fill="#f2f3f6")
        y += height
    result = BytesIO()
    image.save(result, format="PNG")
    result.seek(0)
    return result

EMOJI = re.compile(
    r"[\U0001f1e6-\U0001f1ff]{2}|[0-9#*]\ufe0f?\u20e3|"
    r"[\U0001f300-\U0001faff\u2600-\u27bf](?:\ufe0f|[\U0001f3fb-\U0001f3ff])*"
    r"(?:\u200d[\U0001f300-\U0001faff\u2600-\u27bf](?:\ufe0f|[\U0001f3fb-\U0001f3ff])*)*"
)


def room_card(guild, room, owner, locked, hidden, server_icon=None, owner_avatar=None):
    image = Image.new("RGB", (640, 430), "#111318")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 640, 6), fill="#c4a675")

    def text(value, xy, size=25, fill="#f2f3f6", width=580):
        font = ImageFont.truetype(str(FONT), size)
        emoji_font = ImageFont.truetype(str(ASSETS / "NotoEmoji.ttf"), size)
        value = " ".join(value.split())
        parts = []
        offset = 0
        for match in EMOJI.finditer(value):
            if match.start() > offset:
                parts.append((value[offset:match.start()], font, None))
            token = match.group()
            code = "-".join(f"{ord(char):x}" for char in token if char != "\ufe0f")
            icon = ASSETS / "application-emoji" / f"{code}.png"
            parts.append((token, emoji_font, icon if icon.is_file() else None))
            offset = match.end()
        if offset < len(value):
            parts.append((value[offset:], font, None))
        x, y = xy
        end = x + width
        lengths = [size + 4 if icon else face.getlength(segment) for segment, face, icon in parts]
        truncated = sum(lengths) > width
        if truncated:
            end -= font.getlength("…")
        for (segment, face, icon), length in zip(parts, lengths):
            if x + length > end:
                if face is font:
                    while segment and x + font.getlength(segment) > end:
                        segment = segment[:-1]
                    draw.text((x, y + size), segment.rstrip(), font=font, fill=fill, anchor="ls")
                    x += font.getlength(segment.rstrip())
                break
            if icon:
                with Image.open(icon) as file:
                    badge = file.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
                    image.paste(badge, (round(x), y + 3), badge)
            else:
                draw.text((x, y + size), segment, font=face, fill=fill, anchor="ls")
            x += length
        if truncated:
            draw.text((x, y + size), "…", font=font, fill=fill, anchor="ls")

    def avatar(data, x, y, size, name):
        mask = Image.new("L", (size, size))
        ImageDraw.Draw(mask).ellipse((0, 0, size - 1, size - 1), fill=255)
        if data:
            try:
                with Image.open(BytesIO(data)) as source:
                    picture = ImageOps.fit(source.convert("RGB"), (size, size))
                image.paste(picture, (x, y), mask)
                return
            except (OSError, ValueError):
                pass
        draw.ellipse((x, y, x + size - 1, y + size - 1), fill="#272b35")
        initials = next((char.upper() for char in name if char.isalnum()), "?")
        face = ImageFont.truetype(str(FONT), size // 2)
        draw.text((x + size / 2, y + size / 2), initials, font=face, fill="#dfbd84", anchor="mm")

    avatar(server_icon, 24, 27, 58, guild)
    text(guild, (96, 29), 20, "#c7cbd2", 510)
    text(room.name, (96, 55), 31, width=510)
    draw.line((24, 106, 616, 106), fill="#343843")
    avatar(owner_avatar, 24, 123, 72, owner.display_name)
    text("ВЛАДЕЛЕЦ", (113, 125), 17, "#c4a675", 490)
    text(owner.display_name, (113, 151), 30, width=490)
    fields = (
        ("ВИДИМОСТЬ", "Скрыта" if hidden else "Видна"),
        ("ВХОД", "Закрыт" if locked else "Открыт"),
        ("ЛИМИТ", str(room.user_limit) if room.user_limit else "Без лимита"),
    )
    for index, (title, value) in enumerate(fields):
        x = 24 + index * 200
        draw.rounded_rectangle((x, 226, x + 192, 332), 12, fill="#1b1e25")
        text(title, (x + 12, 242), 17, "#c4a675", 170)
        text(value, (x + 12, 280), 23, width=170)
    text("Black Bot", (24, 374), 20, "#c7cbd2")
    result = BytesIO()
    image.save(result, "PNG")
    result.seek(0)
    return result
