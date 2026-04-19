import requests
import json

payload = {
    "url": "https://www.temu.com/br/top-de-linho-decote-em---de--em-estilo-art%C3%ADstico-vintage-pul%C3%B4ver--casual-de--comprida-para--e-outono-g-601103750279222.html?top_gallery_url=https%3A%2F%2Fimg.kwcdn.com%2Fproduct%2Ffancy%2F866a21b6-2c19-4046-b7e2-5ef16993f03f.jpg",
    "escalation_level": 1
}

print("Posting to API...")
r = requests.post("http://127.0.0.1:8000/api/extract", json=payload)
print(f"Status: {r.status_code}")
try:
    print("Response:")
    print(json.dumps(r.json(), indent=2))
except:
    print(r.text)
