from __future__ import annotations

import html
import io
import os
from pathlib import Path

import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from rembg import new_session, remove


# From light details to dark details.
# More characters give smoother brightness transitions.
ASCII_PALETTE = r"$@B%8&WM#*oahkbdpqwmZO0QLCJUYXzcvunxrjft/\|()1{}[]?-_+~<>i!lI;:,"^'."

ALPHA_THRESHOLD = 80
DEFAULT_ASCII_WIDTH = 88

DARK_THEME_COLOR = "#C9D1D9"
LIGHT_THEME_COLOR = "#24292F"

OUTPUT_DARK = Path("assets/ascii-avatar-dark-v3.svg")
OUTPUT_LIGHT = Path("assets/ascii-avatar-light-v3.svg")
OUTPUT_PNG = Path("assets/avatar-no-background-v3.png")


def download_github_avatar(username: str, token: str | None) -> Image.Image:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "profile-ascii-avatar-generator",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    user_response = requests.get(
        f"https://api.github.com/users/{username}",
        headers=headers,
        timeout=30,
    )
    user_response.raise_for_status()

    avatar_url = user_response.json().get("avatar_url")
    if not avatar_url:
        raise RuntimeError("GitHub API did not return avatar_url.")

    avatar_response = requests.get(
        avatar_url,
        headers={"User-Agent": "profile-ascii-avatar-generator"},
        params={"s": "512"},
        timeout=30,
    )
    avatar_response.raise_for_status()

    image = Image.open(io.BytesIO(avatar_response.content))
    image.load()
    return image.convert("RGBA")


def remove_background(image: Image.Image) -> Image.Image:
    model_name = os.getenv("REMBG_MODEL", "birefnet-portrait")
    session = new_session(model_name)

    result = remove(
        image,
        session=session,
        alpha_matting=True,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=20,
        alpha_matting_erode_size=8,
    )

    if isinstance(result, Image.Image):
        return result.convert("RGBA")

    return Image.open(io.BytesIO(result)).convert("RGBA")


def clean_and_crop(image: Image.Image) -> Image.Image:
    image = image.convert("RGBA")
    alpha = image.getchannel("A")

    # Removes tiny transparent noise that can create a huge empty SVG canvas.
    alpha = alpha.filter(ImageFilter.MedianFilter(size=3))
    alpha = alpha.point(lambda value: value if value >= ALPHA_THRESHOLD else 0)
    image.putalpha(alpha)

    binary_mask = alpha.point(
        lambda value: 255 if value >= ALPHA_THRESHOLD else 0
    )
    bounding_box = binary_mask.getbbox()

    if bounding_box is None:
        raise RuntimeError(
            "The background-removal result is empty. "
            "Lower ALPHA_THRESHOLD to 60 and run the workflow again."
        )

    cropped = image.crop(bounding_box)

    padding = max(4, round(max(cropped.size) * 0.025))
    output = Image.new(
        "RGBA",
        (cropped.width + padding * 2, cropped.height + padding * 2),
        (0, 0, 0, 0),
    )
    output.alpha_composite(cropped, (padding, padding))
    return output


def convert_to_ascii(image: Image.Image, ascii_width: int) -> list[str]:
    # Monospace characters are taller than they are wide.
    character_aspect_ratio = 0.46

    ascii_height = max(
        1,
        round(
            ascii_width
            * image.height
            / image.width
            * character_aspect_ratio
        ),
    )

    small = image.resize(
        (ascii_width, ascii_height),
        Image.Resampling.LANCZOS,
    )

    alpha = small.getchannel("A")
    gray = ImageOps.grayscale(small)
    gray = ImageOps.autocontrast(gray, cutoff=1, mask=alpha)
    gray = ImageEnhance.Contrast(gray).enhance(1.85)
    gray = ImageEnhance.Sharpness(gray).enhance(2.2)

    edge_map = gray.filter(ImageFilter.FIND_EDGES)
    edge_map = ImageOps.autocontrast(edge_map, cutoff=2)

    # Do not use the leading space for opaque portrait pixels.
    visible_palette = ASCII_PALETTE[1:]
    rows: list[str] = []

    for y in range(ascii_height):
        characters: list[str] = []

        for x in range(ascii_width):
            opacity = alpha.getpixel((x, y))

            if opacity < ALPHA_THRESHOLD:
                characters.append(" ")
                continue

            brightness = gray.getpixel((x, y))
            edge_strength = edge_map.getpixel((x, y))

            darkness = 255 - brightness
            density = max(darkness, round(edge_strength * 0.90))

            palette_index = round(
                density / 255 * (len(visible_palette) - 1)
            )
            palette_index = max(
                0,
                min(palette_index, len(visible_palette) - 1),
            )

            characters.append(visible_palette[palette_index])

        rows.append("".join(characters).rstrip())

    while rows and not rows[0].strip():
        rows.pop(0)

    while rows and not rows[-1].strip():
        rows.pop()

    if not rows:
        raise RuntimeError("No ASCII portrait was generated.")

    non_empty_rows = [row for row in rows if row.strip()]
    shared_left_padding = min(
        len(row) - len(row.lstrip())
        for row in non_empty_rows
    )
    rows = [row[shared_left_padding:] for row in rows]

    return rows


def save_svg(lines: list[str], destination: Path, color: str) -> None:
    font_size = 9.5
    line_height = 9.8
    character_width = 5.65
    margin = 4

    column_count = max(len(line) for line in lines)
    svg_width = round(column_count * character_width + margin * 2)
    svg_height = round(len(lines) * line_height + margin * 2)

    tspans: list[str] = []

    for index, line in enumerate(lines):
        escaped_line = html.escape(line)
        dy = "0" if index == 0 else f"{line_height}"
        tspans.append(
            f'<tspan x="{margin}" dy="{dy}">{escaped_line}</tspan>'
        )

    text_rows = "\n    ".join(tspans)

    # No background rectangle is added. The SVG remains transparent.
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg"
     width="{svg_width}"
     height="{svg_height}"
     viewBox="0 0 {svg_width} {svg_height}"
     role="img"
     aria-label="ASCII portrait">
  <text
      x="{margin}"
      y="{margin + font_size}"
      xml:space="preserve"
      fill="{color}"
      font-size="{font_size}"
      font-family="Consolas, Monaco, 'Liberation Mono', monospace"
      font-weight="500">
    {text_rows}
  </text>
</svg>
"""

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(svg, encoding="utf-8")


def main() -> None:
    username = os.getenv("GITHUB_USERNAME")
    if not username:
        raise RuntimeError("GITHUB_USERNAME environment variable is missing.")

    token = os.getenv("GITHUB_TOKEN")
    width_text = os.getenv(
        "ASCII_WIDTH",
        str(DEFAULT_ASCII_WIDTH),
    )

    try:
        ascii_width = int(width_text)
    except ValueError as error:
        raise RuntimeError("ASCII_WIDTH must be an integer.") from error

    if not 40 <= ascii_width <= 120:
        raise RuntimeError("ASCII_WIDTH must be between 40 and 120.")

    print(f"GitHub username: {username}")
    print(f"ASCII width: {ascii_width}")
    print(f"ASCII palette: {ASCII_PALETTE}")
    print(f"Dark SVG color: {DARK_THEME_COLOR}")
    print(f"Light SVG color: {LIGHT_THEME_COLOR}")

    print("Downloading GitHub profile avatar...")
    original = download_github_avatar(username, token)

    print("Removing the background...")
    portrait = remove_background(original)
    portrait = clean_and_crop(portrait)

    OUTPUT_PNG.parent.mkdir(parents=True, exist_ok=True)
    portrait.save(OUTPUT_PNG)

    print("Converting the portrait to ASCII...")
    ascii_lines = convert_to_ascii(portrait, ascii_width)

    save_svg(ascii_lines, OUTPUT_DARK, DARK_THEME_COLOR)
    save_svg(ascii_lines, OUTPUT_LIGHT, LIGHT_THEME_COLOR)

    print(f"Created: {OUTPUT_DARK}")
    print(f"Created: {OUTPUT_LIGHT}")
    print(f"Created: {OUTPUT_PNG}")


if __name__ == "__main__":
    main()
