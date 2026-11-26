import { createCanvas, GlobalFonts } from '@napi-rs/canvas';
import { fileURLToPath } from 'node:url';
import { fields } from './settings.js';

GlobalFonts.registerFromPath(fileURLToPath(new URL('../assets/NotoSans-Regular.ttf', import.meta.url)), 'Squad');
export function applicationCard(id, user, data) {
  const canvas = createCanvas(920, 100);
  const ctx = canvas.getContext('2d');
  ctx.font = '23px Squad';
  const rows = fields.map(([key, label]) => {
    const lines = [];
    for (const paragraph of data[key].replace(/\s+/gu, ' ').split('\n')) {
      let line = '';
      for (const character of paragraph) {
        if (ctx.measureText(line + character).width > 784) {
          lines.push(line.trimEnd());
          line = '';
        }
        line += character;
      }
      lines.push(line.trimEnd());
    }
    return { label, lines };
  });
  canvas.height = 190 + rows.reduce((sum, row) => sum + 78 + row.lines.length * 32, 0);
  ctx.fillStyle = '#111318';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#c4a675';
  ctx.fillRect(48, 42, 52, 4);
  ctx.font = '38px Squad';
  ctx.fillText('BLACK SQUAD', 48, 96);
  ctx.fillStyle = '#c7cbd2';
  ctx.font = '21px Squad';
  ctx.fillText('Заявка №' + id + '  /  ' + user, 48, 137, 824);
  let y = 170;
  rows.forEach(({ label, lines }, index) => {
    const height = 78 + lines.length * 32;
    ctx.fillStyle = '#1b1e25';
    ctx.fillRect(48, y, 824, height - 14);
    ctx.fillStyle = '#dfbd84';
    ctx.font = '20px Squad';
    ctx.fillText(String(index + 1).padStart(2, '0') + '  ' + label, 68, y + 31);
    ctx.fillStyle = '#f2f3f6';
    ctx.font = '23px Squad';
    lines.forEach((line, lineIndex) => ctx.fillText(line, 68, y + 65 + lineIndex * 32));
    y += height;
  });
  return canvas.toBuffer('image/png');
}
