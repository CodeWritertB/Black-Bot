from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

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

def room_card(guild, room, owner, locked, hidden):
    # Layout adapted from Hunter Bot's owner and room status card.
    image = Image.new("RGB", (640, 430), "#111318")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 640, 6), fill="#c4a675")

    def text(value, xy, size=25, fill="#f2f3f6", width=580):
        font = ImageFont.truetype(str(FONT), size)
        if font.getlength(value) > width:
            while value and font.getlength(value + "…") > width:
                value = value[:-1]
            value = value.rstrip() + "…"
        draw.text(xy, value, font=font, fill=fill)

    text(guild, (24, 24), 22, "#c7cbd2")
    text(room.name, (24, 64), 35)
    text("ВЛАДЕЛЕЦ", (24, 134), 18, "#c4a675")
    text(owner.display_name, (24, 161), 30)
    fields = (
        ("ВИДИМОСТЬ", "Скрыта" if hidden else "Видна"),
        ("ВХОД", "Закрыт" if locked else "Открыт"),
        ("ЛИМИТ", str(room.user_limit) if room.user_limit else "Без лимита"),
    )
    for index, (title, value) in enumerate(fields):
        x = 24 + index * 200
        draw.rounded_rectangle((x, 240, x + 192, 332), 12, fill="#1b1e25")
        text(title, (x + 12, 251), 17, "#c4a675", 170)
        text(value, (x + 12, 282), 23, width=170)
    text("Black Bot", (24, 381), 20, "#c7cbd2")
    result = BytesIO()
    image.save(result, "PNG")
    result.seek(0)
    return result
