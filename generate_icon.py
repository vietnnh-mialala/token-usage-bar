"""Generate icon.ico for the packaged executable — an on-brand usage-gauge mark
(dark rounded panel + a teal ring), rendered supersampled then downscaled."""
from PIL import Image, ImageDraw

SS = 4
S = 256 * SS
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# rounded dark panel (the widget's glass navy)
d.rounded_rectangle([8 * SS, 8 * SS, S - 8 * SS, S - 8 * SS],
                    radius=48 * SS, fill=(14, 20, 34, 255),
                    outline=(38, 51, 77, 255), width=3 * SS)

# teal usage ring (arc ~70%) — the gauge motif
cx = cy = S / 2
r = 78 * SS
w = 22 * SS
box = [cx - r, cy - r, cx + r, cy + r]
d.arc(box, 0, 360, fill=(27, 35, 52, 255), width=w)          # track
d.arc(box, -90, -90 + 0.70 * 360, fill=(33, 230, 193, 255), width=w)  # fill

# small centre dot (the live indicator)
dr = 16 * SS
d.ellipse([cx - dr, cy - dr, cx + dr, cy + dr], fill=(33, 230, 193, 255))

img = img.resize((256, 256), Image.LANCZOS)
img.save("icon.ico", sizes=[(256, 256), (128, 128), (64, 64),
                            (48, 48), (32, 32), (16, 16)])
print("wrote icon.ico")
