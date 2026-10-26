"""Prepare the owner's image as a transparent, multi-size Windows icon."""
import argparse
import os
from pathlib import Path
import tempfile

from PIL import Image, ImageOps, UnidentifiedImageError

ICON_SIZES = [(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 128, 256)]


def create_icon(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError('Исходный логотип и готовый ICO должны быть разными файлами.')
    try:
        with Image.open(source) as original:
            image = ImageOps.exif_transpose(original).convert('RGBA')
            image = ImageOps.contain(image, (256, 256), Image.Resampling.LANCZOS)
    except (OSError, UnidentifiedImageError, ValueError) as error:
        raise ValueError(f'Не удалось прочитать логотип: {error}') from error
    # Pad a non-square logo instead of cropping or stretching the owner's artwork.
    canvas = Image.new('RGBA', (256, 256), (0, 0, 0, 0))
    canvas.alpha_composite(image, ((256 - image.width) // 2, (256 - image.height) // 2))
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.app-icon-', suffix='.ico', dir=target.parent, delete=False) as file:
        temporary = Path(file.name)
    try:
        canvas.save(temporary, format='ICO', sizes=ICON_SIZES)
        with Image.open(temporary) as icon:
            if icon.ico.sizes() != set(ICON_SIZES):
                raise ValueError('Не удалось создать все размеры значка Windows.')
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='PNG, ICO, WebP или другой растровый логотип')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent / 'assets' / 'app-icon.ico')
    args = parser.parse_args()
    try:
        target = create_icon(args.source, args.output)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    print(f'Иконка готова: {target}. Размеры: ' + ', '.join(str(size[0]) for size in ICON_SIZES))


if __name__ == '__main__':
    main()
