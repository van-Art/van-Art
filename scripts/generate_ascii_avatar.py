from __future__ import annotations

import html
import io
import os
from pathlib import Path

import requests
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from rembg import new_session, remove


ASCII_PALETTE = " .:-=+*#%@"
ALPHA_THRESHOLD = 45
DEFAULT_WIDTH = 64


def download_github_avatar(username: str, token: str | None) -> Image.Image:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "github-ascii-avatar-generator",
    }

    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(
        f"https://api.github.com/users/{username}",
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()

    avatar_url = response.json().get("avatar_url")
    if not avatar_url:
        raise RuntimeError("GitHub API did not return avatar_url.")

    avatar_response = requests.get(
        avatar_url,
        headers={"User-Agent": "github-ascii-avatar-generator"},
        timeout=30,
    )
    avatar_response.raise_for_status()

    image = Image.open(io.BytesIO(avatar_response.content))
    image.load()
    return image.convert("RGBA")


def remove_avatar_background(image: Image.Image) -> Image.Image:
    model_name = os.getenv("REMBG_MODEL", "birefnet-portrait")
    session = new_session(model_name)
    result = remove(image, session=session)

    if isinstance(result, Image.Image):
        return result.convert("RGBA")

    return Image.open(io.BytesIO(result)).convert("RGBA")


def crop_transparent_area(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    box = alpha.getbbox()

    if box is None:
        raise RuntimeError("Background removal produced an empty image.")

    cropped = image.crop(box)
    padding = max(6, int(max(cropped.size) * 0.06))

    output = Image.new(
        "RGBA",
        (cropped.width + padding * 2, cropped.height + padding * 2),
        (0, 0, 0, 0),
    )
    output.alpha_composite(cropped, (padding, padding))
    return output


def convert_to_ascii(image: Image.Image, width: int) -> list[str]:
    # Monospace characters are taller than they are wide.
    aspect_correction = 0.50
    height = max(
        1,
        round(width * image.height / image.width * aspect_correction),
    )

    small = image.resize((width, height), Image.Resampling.LANCZOS)
    alpha = small.getchannel("A")

    gray = ImageOps.grayscale(small)
    gray = ImageOps.autocontrast(gray, cutoff=1, mask=alpha)
    gray = ImageEnhance.Contrast(gray).enhance(1.75)
    gray = ImageEnhance.Sharpness(gray).enhance(2.2)

    edges = gray.filter(ImageFilter.FIND_EDGES)
    edges = ImageOps.autocontrast(edges, cutoff=2)

    lines: list[str] = []

    for y in range(height):
        row: list[str] = []

        for x in range(width):
            alpha_value = alpha.getpixel((x, y))

            # Transparent pixels are ordinary spaces, so the background
            # is not drawn in the ASCII avatar.
            if alpha_value < ALPHA_THRESHOLD:
                row.append(" ")
                continue

            brightness = gray.getpixel((x, y))
            edge_strength = edges.getpixel((x, y))

            darkness = 255 - brightness
            density = max(darkness, round(edge_strength * 0.85))

            index = 1 + round(
                density / 255 * (len(ASCII_PALETTE) - 2)
            )
            index = min(index, len(ASCII_PALETTE) - 1)
            row.append(ASCII_PALETTE[index])

        lines.append("".join(row).rstrip())

    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        raise RuntimeError("No ASCII content was generated.")

    return lines


def save_svg(lines: list[str], output_path: Path, text_color: str) -> None:
    font_size = 12
    line_height = 13
    character_width = 7.3
    margin = 10

    columns = max(len(line) for line in lines)
    width = round(columns * character_width + margin * 2)
    height = round(len(lines) * line_height + margin * 2)

    tspans: list[str] = []
    for index, line in enumerate(lines):
        escaped = html.escape(line)
        dy = "0" if index == 0 else str(line_height)
        tspans.append(
            f'<tspan x="{margin}" dy="{dy}">{escaped}</tspan>'
        )

    # There is intentionally no <rect>, so the SVG background is transparent.
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg"
    width="{width}"
    height="{height}"
    viewBox="0 0 {width} {height}"
    role="img"
    aria-label="ASCII profile avatar">
  <text
      x="{margin}"
      y="{margin + font_size}"
      xml:space="preserve"
      fill="{text_color}"
      font-size="{font_size}"
      font-family="Consolas, Monaco, 'Liberation Mono', monospace"
      font-weight="500">
    {'\n    '.join(tspans)}
  </text>
</svg>
"""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(svg, encoding="utf-8")


def main() -> None:
    username = os.getenv("GITHUB_USERNAME")
    if not username:
        raise RuntimeError("GITHUB_USERNAME is missing.")

    token = os.getenv("GITHUB_TOKEN")
    model_name = os.getenv("REMBG_MODEL", "birefnet-portrait")
    width_text = os.getenv("ASCII_WIDTH", str(DEFAULT_WIDTH))

    try:
        width = int(width_text)
    except ValueError as error:
        raise RuntimeError("ASCII_WIDTH must be a number.") from error

    if not 36 <= width <= 90:
        raise RuntimeError("ASCII_WIDTH must be between 36 and 90.")

    print(f"Downloading avatar for {username}...")
    original = download_github_avatar(username, token)

    print(f"Removing background with {model_name}...")
    portrait = remove_avatar_background(original)
    portrait = crop_transparent_area(portrait)

    assets = Path("assets")
    assets.mkdir(parents=True, exist_ok=True)
    portrait.save(assets / "avatar-no-background.png")

    print("Generating ASCII...")
    lines = convert_to_ascii(portrait, width)

    save_svg(lines, assets / "ascii-avatar-dark.svg", "#C9D1D9")
    save_svg(lines, assets / "ascii-avatar-light.svg", "#24292F")

    print("Done.")


if __name__ == "__main__":
    main()
