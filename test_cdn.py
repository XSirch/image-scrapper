import requests, re
from PIL import Image
import io

shopid = 940483748
itemid = 22597773566

s = requests.Session()
s.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Accept": "text/html,application/xhtml+xml",
})

r = s.get(f"https://shopee.com.br/product/{shopid}/{itemid}")
print(f"Status: {r.status_code}")

# Extract OG image
og = re.findall(r'property="og:image"[^>]*content="([^"]+)"', r.text)
if not og:
    og = re.findall(r'content="([^"]+)"[^>]*property="og:image"', r.text)
print(f"OG Image: {og}")

# Extract all susercontent image hashes
all_refs = set(re.findall(r'down-br\.img\.susercontent\.com/file/([a-zA-Z0-9_-]+)', r.text))
print(f"\nUnique image hashes: {len(all_refs)}")

# Check actual size of each
product_imgs = []
for h in all_refs:
    img_url = f"https://down-br.img.susercontent.com/file/{h}"
    try:
        r2 = requests.get(img_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r2.status_code == 200:
            pil_img = Image.open(io.BytesIO(r2.content))
            w, ht = pil_img.size
            if min(w, ht) >= 200:
                product_imgs.append(img_url)
                print(f"  PRODUCT {w}x{ht}: {h}")
    except:
        pass

print(f"\nTotal product images: {len(product_imgs)}")
