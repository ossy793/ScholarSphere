"""
Run this once on the server to generate PWA icons:
  python3 generate_icons.py
Requires: Pillow  (pip install Pillow)
Output:   frontend/icons/icon-192.png  and  frontend/icons/icon-512.png
"""
import os
from PIL import Image, ImageDraw, ImageFont

os.makedirs("frontend/icons", exist_ok=True)

def make_icon(size):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Blue gradient background (rounded square)
    radius = size // 4
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill="#0077FF")

    # Draw "P" text
    font_size = int(size * 0.60)
    try:
        font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    text = "P"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (size - tw) // 2 - bbox[0]
    y = (size - th) // 2 - bbox[1] - int(size * 0.04)
    draw.text((x, y), text, fill="white", font=font)

    return img

make_icon(192).save("frontend/icons/icon-192.png")
make_icon(512).save("frontend/icons/icon-512.png")
print("Icons created: frontend/icons/icon-192.png, frontend/icons/icon-512.png")
