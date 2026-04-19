import urllib.parse
import re

url = "https://www.temu.com/br/top-de-linho-decote-em---de--em-estilo-art%C3%ADstico-vintage-pul%C3%B4ver--casual-de--comprida-para--e-outono-g-601103750279222.html?top_gallery_url=https%3A%2F%2Fimg.kwcdn.com%2Fproduct%2Ffancy%2F866a21b6-2c19-4046-b7e2-5ef16993f03f.jpg"
parsed = urllib.parse.urlparse(url)
qs = urllib.parse.parse_qs(parsed.query)

found_imgs = []
for k, v in qs.items():
    for val in v:
        if val.startswith("http") and any(ext in val.lower() for ext in [".jpg", ".png", ".jpeg", ".webp"]):
            found_imgs.append(val)

print("Images inside URL:", found_imgs)
