"""Compare two folders of UI stand screenshots pixel by pixel.

    python -X utf8 -B tools/ui_compare.py <before dir> <after dir> [--out <diff dir>] [--threshold 0.1]

For every screenshot present in both folders it counts the pixels that differ (any channel by more
than 16 of 255), writes a side-by-side «before | after | difference» image for the changed ones and
a Markdown table `_compare.md` sorted by the share of changed pixels. Screenshots present in only
one folder are listed too. The exit code is 1 when a screenshot changed by more than `--threshold`
percent, so a change meant to be invisible can be checked by the code alone.
"""
import argparse
from pathlib import Path
import sys

from PIL import Image, ImageChops


def changed_share(before, after):
    """Share of changed pixels and the mask of them; a different size counts as fully changed."""
    if before.size != after.size:
        return 1.0, None
    diff = ImageChops.difference(before.convert('RGB'), after.convert('RGB'))
    mask = diff.convert('L').point(lambda value: 255 if value > 16 else 0)
    changed = mask.histogram()[255]
    return changed / (before.size[0] * before.size[1]), mask


def side_by_side(before, after, mask):
    width, height = before.size
    sheet = Image.new('RGB', (width * 3, height), 'white')
    sheet.paste(before.convert('RGB'), (0, 0))
    sheet.paste(after.convert('RGB'), (width, 0))
    shade = after.convert('RGB').point(lambda value: 160 + value * 95 // 255)
    red = Image.new('RGB', (width, height), (220, 30, 60))
    sheet.paste(Image.composite(red, shade, mask), (width * 2, 0))
    return sheet


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('before')
    parser.add_argument('after')
    parser.add_argument('--out')
    parser.add_argument('--threshold', type=float, default=0.1, help='percent of changed pixels')
    args = parser.parse_args()
    before_dir, after_dir = Path(args.before), Path(args.after)
    out = Path(args.out) if args.out else after_dir / '_diff'
    out.mkdir(parents=True, exist_ok=True)
    names_before = {path.name for path in before_dir.glob('*.png')}
    names_after = {path.name for path in after_dir.glob('*.png')}
    rows, over = [], 0
    for name in sorted(names_before & names_after):
        with Image.open(before_dir / name) as before, Image.open(after_dir / name) as after:
            share, mask = changed_share(before, after)
            if share and mask is not None:
                side_by_side(before, after, mask).save(out / name)
        percent = share * 100
        if percent > args.threshold:
            over += 1
        rows.append((percent, name))
    rows.sort(reverse=True)
    lines = [f'# Сравнение снимков: {before_dir} → {after_dir}', '',
             f'Порог: {args.threshold} % изменённых пикселей. Выше порога: {over} из {len(rows)}.', '',
             '| Изменено, % | Снимок |', '|---:|---|']
    lines += [f'| {percent:.3f} | {f"[{name}]({name})" if percent else name} |' for percent, name in rows]
    for title, names in (('Только «до»', names_before - names_after), ('Только «после»', names_after - names_before)):
        if names:
            lines += ['', f'## {title}', ''] + [f'- {name}' for name in sorted(names)]
    (out / '_compare.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'{over} of {len(rows)} screenshots changed by more than {args.threshold} %; report: {out / "_compare.md"}')
    return 1 if over else 0


if __name__ == '__main__':
    sys.exit(main())
