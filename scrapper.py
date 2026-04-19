import logging
import argparse
import sys
import json
import os
from urllib.parse import urlparse, urljoin
from scrapling.fetchers import StealthyFetcher

MEMORY_FILE = 'site_profiles.json'

import database

def mark_escalation_required(domain, level):
    try:
        wait_idle = level >= 2
        database.upsert_profile(domain, level, wait_idle)
    except Exception as e:
        logging.error(f"Failed to update profile memory for {domain}: {e}")

def _extract_via_googlebot(url, domain):
    """
    Fallback para sites com Login Wall (ex: Shopee).
    Faz uma requisição simples com User-Agent do Googlebot, que recebe a versão SSR (Server-Side Rendered) da página,
    contendo os dados do produto embutidos para indexação. Extrai os hashes de imagem do CDN e filtra por resolução real.
    """
    import requests
    import re
    
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
        "Accept": "text/html,application/xhtml+xml",
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code != 200:
            logging.info(f"[{domain}] Googlebot SSR falhou: status {r.status_code}")
            return []
    except Exception as e:
        logging.info(f"[{domain}] Googlebot SSR erro: {e}")
        return []
    
    html = r.text
    
    # Detectar domínio de CDN de imagens (ex: down-br.img.susercontent.com)
    cdn_pattern = r'(down-[a-z]+\.img\.susercontent\.com)/file/([a-zA-Z0-9_-]+)'
    matches = re.findall(cdn_pattern, html)
    
    if not matches:
        logging.info(f"[{domain}] Googlebot SSR: nenhum CDN de imagens encontrado no HTML.")
        return []
    
    # Agrupar por CDN host e pegar hashes únicos
    cdn_host = matches[0][0]
    unique_hashes = list(set(h for _, h in matches))
    logging.info(f"[{domain}] Googlebot SSR: {len(unique_hashes)} hashes únicos encontrados no CDN {cdn_host}")
    
    # Filtrar por resolução real (baixar header de cada imagem e verificar dimensões)
    product_images = []
    for h in unique_hashes:
        img_url = f"https://{cdn_host}/file/{h}"
        try:
            from PIL import Image
            import io
            resp = requests.get(img_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
            if resp.status_code == 200:
                img = Image.open(io.BytesIO(resp.content))
                w, ht = img.size
                if min(w, ht) >= 200:
                    product_images.append(img_url)
                    
        except Exception:
            pass
    
    logging.info(f"[{domain}] Googlebot SSR: {len(product_images)} imagens de produto (>= 200px) encontradas!")
    return product_images

# Fashion product pages generally have large images in galleries.
# We will use Scrapling's StealthyFetcher to bypass possible basic bot protections.
def extract_product_images(url, session=None, wait_idle=False, escalation_level=1):
    """
    Extracts product images from a fashion e-commerce URL using heuristics.
    """
    domain = urlparse(url).netloc
    if domain.startswith('www.'):
        domain = domain[4:]

    # Verificar memória de perfil de site    
    try:
        profile = database.get_profile(domain)
    except Exception as e:
        logging.error(f"Failed to fetch profile for {domain}: {e}")
        profile = {}
        
    saved_level = profile.get('escalation_level', 1)
    
    # Se o nível exigido por clique (GUI) for maior do que o salvo, a gente aprende permanentemente a nova tática.
    if escalation_level > saved_level:
        logging.info(f"[{domain}] Memorizando nova tática agressiva de extração (Nível {escalation_level}).")
        mark_escalation_required(domain, escalation_level)
    # Se o nível que a gente sabe que o site precisa for maior do que o atual, aplicamos automaticamente (Magia da Memória!)
    elif saved_level > escalation_level:
        logging.info(f"[{domain}] Memória carregada: Aplicando escalada automática para Nível {saved_level}.")
        escalation_level = saved_level
        
    if escalation_level >= 2:
        logging.info(f"[{domain}] Escalation Level {escalation_level}: Forçando hidratação pesada (wait_idle=True)")
        wait_idle = True
        
    # Manter compatibilidade com a versão antiga do profiles
    if not wait_idle and profile.get('wait_idle', False):
         logging.info(f"[{domain}] Memória carregada: Redirecionando para busca JS/SPA pesada (wait_idle=True).")
         wait_idle = True

    images = set()

    # Pre-fetch Heuristics: Some sites (like Temu) encode product images directly into the URL query parameters!
    import urllib.parse
    qs = urllib.parse.parse_qs(urlparse(url).query)
    for key, values in qs.items():
        for val in values:
            if val.startswith('http') and any(ext in val.lower() for ext in ['.jpg', '.jpeg', '.png', '.webp']):
                logging.info(f"[{domain}] Imagem encontrada na URL (parâmetro {key})!")
                images.add(val)

    # Fetch content (support persistent session for performance)
    try:
        if session:
            page = session.fetch(url, network_idle=wait_idle)
        else:
            page = StealthyFetcher.fetch(url, headless=True, network_idle=wait_idle)
            
        # Anti-Bot Wall Detection & Recovery via Googlebot SSR (Login, Captcha, Traffic)
        # Anti-Bot Wall Detection & Recovery via Googlebot SSR (Login, Captcha, Traffic)
        page_url_lower = page.url.lower()
        if 'login' in page_url_lower or 'signin' in page_url_lower or 'verify' in page_url_lower or 'captcha' in page_url_lower:
            logging.info(f"[{domain}] Anti-Bot/Login Wall Detectado! ({page.url})")
            logging.info(f"[{domain}] Ativando fallback Googlebot SSR para extrair imagens do cache de SEO...")
            fallback_images = _extract_via_googlebot(url, domain)
            images.update(fallback_images)
            return list(images)
            
    except Exception as e:
        logging.info(f"Error fetching URL: {e}")
        return list(images)

    # Noise keywords to filter out UI elements, banners, footer items, and size tables
    if escalation_level >= 4:
        logging.info(f"[{domain}] Escalation Level 4: Removendo todos os filtros de ruído.")
        noise_keywords = []
    else:
        noise_keywords = ['icon', 'spinner', 'logo', 'footer', 'banner', 'badge', 'menu', 'nav', 'button', 'promo', 'assets-builder', 'table', 'tabela', 'guia', 'size']

    # Heuristic 1: Extract OpenGraph image (usually the main product image)
    og_images = page.css('meta[property="og:image"]')
    if og_images:
        for og_image in og_images:
            content = og_image.attrib.get('content')
            if content and not any(keyword in content.lower() for keyword in noise_keywords):
                images.add(urljoin(url, content))

    # Heuristic 2: Extract img tags but with strong filtering
    # We ignore standard tiny icons, base64 data, and non-photographic formats
    img_tags = page.css('img')
    for img in img_tags:
        # Check several common attributes for the actual image URL
        src = img.attrib.get('data-zoom-image') or img.attrib.get('data-large') or img.attrib.get('data-src') or img.attrib.get('src') or img.attrib.get('data-original')
        if not src:
            continue

        if src.startswith('data:image'):
            continue
            
        full_url = urljoin(url, src)
        lower_url = full_url.lower()
        
        # Skip non-photographic or layout images
        if '.svg' in lower_url or '.gif' in lower_url:
            continue
            
        # Strongly filter out UI elements, banners, and footer items
        if any(keyword in lower_url for keyword in noise_keywords):
            continue
            
        images.add(full_url)

    # Heuristic 3: Check for pictures or background images in common gallery classes
    # Some sites use elements with background-image for galleries
    gallery_elements = page.css('[class*="gallery"], [class*="product-image"], [id*="product-image"], [class*="productImages"], [class*="productGallery"], [class*="product-images"], [class*="fbits-imagem"], [class*="image-container"]')
    
    gallery_images = set()
    for el in gallery_elements:
        # Check for background-image
        style = el.attrib.get('style', '')
        if 'background-image' in style:
            # Extract URL from url('...')
            import re
            m = re.search(r"url\(['\"]?(.*?)['\"]?\)", style)
            if m:
                bg_url = m.group(1)
                if not bg_url.startswith('data:') and not '.svg' in bg_url.lower() and not '.gif' in bg_url.lower():
                    gallery_images.add(urljoin(url, bg_url))
        
        # Also, if we found a gallery, ONLY get img tags from inside it!
        inside_imgs = el.css('img')
        for img in inside_imgs:
            src = img.attrib.get('data-zoom-image') or img.attrib.get('data-large') or img.attrib.get('data-src') or img.attrib.get('src')
            if src and not src.startswith('data:') and not '.svg' in src.lower() and not '.gif' in src.lower():
                gallery_images.add(urljoin(url, src))

    if gallery_images:
        # If we successfully found a gallery structure, we discard the noise from Heuristic 2
        images = gallery_images
        
    og_urls = set()
    if og_images:
        for og in og_images:
            content = og.attrib.get('content')
            if content and not any(keyword in content.lower() for keyword in noise_keywords):
                og_urls.add(urljoin(url, content))
        if og_urls:
            images.update(og_urls)
            
    # Final Heuristic (Smart Filter): Eliminates "related products"
    # By comparing the filename of the og_image with all other found images.
    # Products variations (angles) usually share the exactly the same SKU base.
    if og_urls:
        if escalation_level >= 3:
            logging.info(f"[{domain}] Escalation Level {escalation_level}: Ignorando Smart Filter de Semelhança. Retornando todas as imagens possíveis.")
            # We skip the Smart Filter drop entirely.
        else:
            import difflib
            og_url = list(og_urls)[0] # Use the first one as a base
            og_filename = og_url.split('/')[-1].split('?')[0]
            
            filtered_images = set(og_urls)
            for img in images:
                if img in og_urls:
                    continue
                img_filename = img.split('/')[-1].split('?')[0]
                similarity = difflib.SequenceMatcher(None, og_filename, img_filename).ratio()
                
                # Threshold needs to be very high (0.90+) because related products might only differ
                # by 1 or 2 digits in the middle of a 15-character SKU (e.g. 53503444ESTP01 vs 53503643ESTP01 -> 0.89)
                if similarity >= 0.90:
                    filtered_images.add(img)
                    
            # Only apply strict filter if we found other similar images, ensuring we don't accidentally drop everything.
            if len(filtered_images) > 1:
                images = filtered_images
            elif not wait_idle:
                # Smart filter failed! This usually means the real images (which match the OG image)
                # haven't hydrated yet in SPA frameworks like Deco.cx or VTEX IO. Retry with full hydration!
                logging.info(f"[{domain}] Aprendendo nova tática! Esse site requer hidratação JS (wait_idle=True).")
                mark_escalation_required(domain, 2)
                return extract_product_images(url, session=session, wait_idle=True, escalation_level=escalation_level)
            
    # Heuristic: CDN Dominance Filter
    # Sites like Shopee serve product images from a dedicated CDN (e.g., susercontent.com)
    # while UI/popup/asset images come from the main domain or other CDNs.
    # If most images share the same CDN host, keep only those.
    if len(images) > 2:
        from collections import Counter
        cdn_hosts = Counter()
        for img_url in images:
            host = urlparse(img_url).netloc
            cdn_hosts[host] += 1
        
        most_common_host, most_common_count = cdn_hosts.most_common(1)[0]
        # If a CDN hosts the majority of images (>50%) and it's NOT the site's own domain,
        # it's likely the product image CDN. Keep only those.
        if most_common_count >= len(images) * 0.5 and most_common_host != domain and 'www.' + most_common_host != domain:
            cdn_images = {img for img in images if urlparse(img).netloc == most_common_host}
            if len(cdn_images) >= 2:
                logging.info(f"[{domain}] CDN Filter: Mantendo {len(cdn_images)} imagens do CDN dominante ({most_common_host}), removendo {len(images) - len(cdn_images)} imagens de outros domínios.")
                images = cdn_images

    return list(images)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fashion Product Image Scrapper using Scrapling")
    parser.add_argument("url", help="URL of the fashion product")
    args = parser.parse_args()

    logging.info(f"Fetching images for: {args.url} ...")
    found_images = extract_product_images(args.url)
    
    if not found_images:
        logging.info("No images found.")
        sys.exit(1)
        
    logging.info("\n--- Available Product Images ---")
    for idx, img_url in enumerate(found_images, 1):
        logging.info(f"[{idx}] {img_url}")
