import qrcode
import os

# ── CONFIG ─────────────────────────────────────────
BASE_URL = "http://127.0.0.1:5000/shop"   # change to your domain when live

shops = [
    "ram-tea-stall",
    "priya-tiffins",
    # add more shop slugs here
]

os.makedirs("static/qrcodes", exist_ok=True)

for slug in shops:
    url = f"{BASE_URL}/{slug}"
    img = qrcode.make(url)
    path = f"static/qrcodes/{slug}.png"
    img.save(path)
    print(f"✅ QR saved: {path}  →  {url}")

print("\nAll QR codes generated in static/qrcodes/")