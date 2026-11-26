from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from storage import FIELDS

FONT = Path(__file__).with_name("assets") / "NotoSans-Regular.ttf"


def application_card(number, username, data):
    regular = ImageFont.truetype(str(FONT), 23)
    label_font = ImageFont.truetype(str(FONT), 20)
    title = ImageFont.truetype(str(FONT), 38)
    rows = []
    for key, (label, _) in FIELDS.items():
        lines, line = [], ""
        for char in " ".join(data[key].split()):
            if regular.getlength(line + char) > 780:
                lines.append(line.rstrip())
                line = ""
            line += char
        lines.append(line.rstrip())
        rows.append((label, lines))
    image = Image.new("RGB", (920, 190 + sum(78 + 32 * len(lines) for _, lines in rows)), "#111318")
    draw = ImageDraw.Draw(image)
    draw.rectangle((48, 42, 100, 46), fill="#c4a675")
    draw.text((48, 57), "BLACK SQUAD", font=title, fill="#dfbd84")
    draw.text((48, 113), f"Заявка №{number}  /  {username}", font=label_font, fill="#c7cbd2")
    y = 170
    for index, (label, lines) in enumerate(rows, 1):
        height = 78 + len(lines) * 32
        draw.rectangle((48, y, 872, y + height - 14), fill="#1b1e25")
        draw.text((68, y + 10), f"{index:02}   {label}", font=label_font, fill="#dfbd84")
        for index_line, line in enumerate(lines):
            draw.text((68, y + 44 + index_line * 32), line, font=regular, fill="#f2f3f6")
        y += height
    result = BytesIO()
    image.save(result, format="PNG")
    result.seek(0)
    return result
