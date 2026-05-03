import logging
import argparse
import sys
import json
import os
import re
import requests
from urllib.parse import urlparse, urljoin
from scrapling.fetchers import StealthyFetcher

MEMORY_FILE = 'site_profiles.json'

# Domínios de encurtadores conhecidos que devem ser resolvidos antes do scraping
SHORT_URL_DOMAINS = ['shp.ee', 'sho.pe', 's.shopee']

import database

SHEIN_PRODUCT_IMAGE_PATHS = (
    '/images3_pi/',
    '/images3_spmp/',
    '/images_pi/',
    '/images_spmp/',
)

SHEIN_IMAGE_KEYS = {
    'goods_img',
    'detail_image',
    'detail_image_url',
    'main_image',
    'main_image_url',
    'image',
    'image_url',
    'images',
    'img_url',
    'src',
}

def resolve_short_url(url):
    """
    Resolve URLs encurtadas (ex: br.shp.ee/xxx) para a URL final do produto.
    Retorna a URL resolvida ou a original se não for um short URL.
    """
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    
    # Verifica se é um domínio de short URL conhecido
    is_short = any(domain.endswith(d) for d in SHORT_URL_DOMAINS)
    if not is_short:
        return url
    
    logging.info(f"[ShortURL] Detectado link encurtado ({domain}). Resolvendo...")
    try:
        r = requests.get(url, allow_redirects=True, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        })
        resolved = r.url
        # Remove tracking params para ter uma URL limpa
        parsed_resolved = urlparse(resolved)
        # Mantém apenas o path essencial
        clean_url = f"{parsed_resolved.scheme}://{parsed_resolved.netloc}{parsed_resolved.path}"
        logging.info(f"[ShortURL] Resolvido: {url} → {clean_url}")
        return clean_url
    except Exception as e:
        logging.error(f"[ShortURL] Falha ao resolver {url}: {e}")
        return url

def mark_escalation_required(domain, level):
    try:
        wait_idle = level >= 2
        database.upsert_profile(domain, level, wait_idle)
    except Exception as e:
        logging.error(f"Failed to update profile memory for {domain}: {e}")

def _domain_from_url(url):
    domain = urlparse(url).netloc
    if domain.startswith('www.'):
        domain = domain[4:]
    return domain

def _retry_escalation_levels(start_level, max_attempts=3):
    start_level = max(1, min(int(start_level or 1), 4))
    levels = []
    level = start_level
    while len(levels) < max_attempts and level <= 4:
        levels.append(level)
        level += 1
    return levels

def _is_shein_domain(domain):
    return 'shein.com' in domain

def _extract_shein_product_params(url):
    parsed = urlparse(url)
    match = re.search(r'-p-(\d+)\.html', parsed.path)
    goods_id = match.group(1) if match else None

    mall_code = None
    try:
        from urllib.parse import parse_qs
        qs = parse_qs(parsed.query)
        mall_values = qs.get('mallCode') or qs.get('mall_code')
        if mall_values:
            mall_code = mall_values[0]
    except Exception:
        mall_code = None

    return goods_id, mall_code

def _normalize_shein_image_url(value):
    if not value or not isinstance(value, str):
        return None

    value = value.strip().strip('"').strip("'")
    value = value.replace('\\/', '/')
    if value.startswith('//'):
        value = f"https:{value}"
    elif value.startswith('http://'):
        value = f"https://{value[len('http://'):]}"

    if not value.startswith('https://'):
        return None

    parsed = urlparse(value)
    if parsed.netloc not in ('img.ltwebstatic.com', 'img.shein.com', 'img.romwe.com'):
        return None

    if not any(path in parsed.path for path in SHEIN_PRODUCT_IMAGE_PATHS):
        return None

    if not re.search(r'\.(jpe?g|png|webp)(?:$|\?)', parsed.path.lower()):
        return None

    return value

def _is_shein_risk_page(page_url='', html=''):
    page_url = (page_url or '').lower()
    html = html or ''
    if '/risk/challenge' in page_url or '/risk/action/limit' in page_url or 'captcha_type=' in page_url:
        return True
    if '/risk/challenge' in html or '/risk/action/limit' in html or 'captcha_type' in html:
        return True
    return False

def _collect_shein_images_from_data(data, images, trusted=False):
    if isinstance(data, dict):
        for key, value in data.items():
            key_trusted = trusted or str(key).lower() in SHEIN_IMAGE_KEYS
            _collect_shein_images_from_data(value, images, trusted=key_trusted)
    elif isinstance(data, list):
        for item in data:
            _collect_shein_images_from_data(item, images, trusted=trusted)
    elif trusted and isinstance(data, str):
        image_url = _normalize_shein_image_url(data)
        if image_url:
            images.add(image_url)

def _extract_shein_images_from_html(html, page_url=''):
    if not html or _is_shein_risk_page(page_url, html):
        return []

    images = set()

    meta_pattern = r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']'
    for match in re.findall(meta_pattern, html, flags=re.IGNORECASE):
        image_url = _normalize_shein_image_url(match)
        if image_url:
            images.add(image_url)

    json_ld_pattern = r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>'
    for script_text in re.findall(json_ld_pattern, html, flags=re.IGNORECASE | re.DOTALL):
        try:
            _collect_shein_images_from_data(json.loads(script_text), images, trusted=True)
        except Exception:
            pass

    key_pattern = r'"(?:goods_img|detail_image|main_image|image_url|img_url|src)"\s*:\s*("(?:\\.|[^"\\])*"|\[(?:\\.|[^\]])*\])'
    for raw_value in re.findall(key_pattern, html, flags=re.IGNORECASE):
        try:
            parsed_value = json.loads(raw_value)
            _collect_shein_images_from_data(parsed_value, images, trusted=True)
        except Exception:
            image_url = _normalize_shein_image_url(raw_value)
            if image_url:
                images.add(image_url)

    direct_pattern = r'(?:https?:)?//img\.ltwebstatic\.com/[^"\'<>\s\\]+'
    for match in re.findall(direct_pattern, html):
        image_url = _normalize_shein_image_url(match)
        if image_url:
            images.add(image_url)

    return list(images)

def _extract_via_shein_api(url, domain, session=None):
    goods_id, mall_code = _extract_shein_product_params(url)
    if not goods_id:
        logging.info(f"[{domain}] SHEIN API: não foi possível extrair goods_id da URL")
        return []

    api_url = f"https://{domain}/api/productInfo/quickView/get?goods_id={goods_id}"
    if mall_code:
        api_url = f"{api_url}&mallCode={mall_code}"

    logging.info(f"[{domain}] SHEIN API: buscando dados best-effort ({api_url})")

    try:
        if session:
            api_page = session.fetch(api_url, network_idle=False)
            page_url = getattr(api_page, 'url', api_url)
            body_text = api_page.css('body')[0].text if api_page.css('body') else ''
            if not body_text:
                pre_tags = api_page.css('pre')
                if pre_tags:
                    body_text = pre_tags[0].text

            if _is_shein_risk_page(page_url, body_text):
                logging.info(f"[{domain}] SHEIN API: bloqueada por página de risco ({page_url})")
                return []

            data = json.loads(body_text)
        else:
            r = requests.get(api_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
                "Referer": url,
            }, timeout=15)

            content_type = r.headers.get('content-type', '').lower()
            if _is_shein_risk_page(r.url, r.text) or 'json' not in content_type:
                logging.info(f"[{domain}] SHEIN API: resposta não JSON ou bloqueada ({r.status_code}, {r.url})")
                return []

            data = r.json()

        images = set()
        _collect_shein_images_from_data(data, images)
        if images:
            logging.info(f"[{domain}] SHEIN API: {len(images)} imagens encontradas!")
            return list(images)

        logging.info(f"[{domain}] SHEIN API: JSON sem imagens de produto")
    except Exception as e:
        logging.info(f"[{domain}] SHEIN API erro: {e}")

    return []

def _extract_via_shein(url, domain, session=None, html=None, page_url=''):
    html_images = _extract_shein_images_from_html(html, page_url=page_url) if html else []
    if html_images:
        logging.info(f"[{domain}] SHEIN HTML: {len(html_images)} imagens encontradas!")
        return html_images

    if html and _is_shein_risk_page(page_url, html):
        logging.info(f"[{domain}] SHEIN HTML: página de risco detectada; ignorando assets de layout.")

    return _extract_via_shein_api(url, domain, session=session)

def _extract_via_shopee_api(url, domain):
    """
    Fallback para Shopee: extrai shop_id e item_id da URL do produto
    e tenta buscar imagens via API interna da Shopee com headers de browser real.
    """
    # Extrair IDs do produto da URL
    match = re.search(r'/product/(\d+)/(\d+)', url)
    if not match:
        match = re.search(r'-i\.(\d+)\.(\d+)', url)
    
    if not match:
        logging.info(f"[{domain}] Shopee API: Não foi possível extrair shop_id/item_id da URL")
        return []
    
    shop_id = match.group(1)
    item_id = match.group(2)
    logging.info(f"[{domain}] Shopee API: Extraído shop_id={shop_id}, item_id={item_id}")
    
    # Tentar via sessão com cookies
    try:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
        })
        # Visitar a homepage para pegar cookies (SPC_F, etc)
        session.get(f"https://{domain}", timeout=10)
        
        api_url = f"https://{domain}/api/v4/pdp/get_pc?shop_id={shop_id}&item_id={item_id}"
        r = session.get(api_url, headers={
            "Referer": f"https://{domain}/product/{shop_id}/{item_id}",
            "X-Requested-With": "XMLHttpRequest",
        }, timeout=15)
        
        if r.status_code == 200:
            data = r.json()
            images = data.get('data', {}).get('images', [])
            if images:
                cdn_host = f"down-br.img.susercontent.com"
                # Detectar o CDN regional correto
                locale = domain.split('.')[-1] if '.' in domain else 'br'
                cdn_host = f"down-{locale}.img.susercontent.com"
                product_images = [f"https://{cdn_host}/file/{h}" for h in images]
                logging.info(f"[{domain}] Shopee API: {len(product_images)} imagens encontradas!")
                return product_images
        
        logging.info(f"[{domain}] Shopee API retornou status {r.status_code}")
    except Exception as e:
        logging.info(f"[{domain}] Shopee API erro: {e}")
    
    return []

def _extract_via_googlebot(url, domain):
    """
    Fallback para sites com Login Wall.
    Tenta múltiplos User-Agents de crawlers conhecidos (Googlebot, Facebook, etc.)
    para obter a versão SSR (Server-Side Rendered) da página com dados do produto.
    A Shopee, por exemplo, bloqueia Googlebot (403) mas serve conteúdo completo
    para o crawler do Facebook (facebookexternalhit).
    """
    
    # Lista de crawlers para tentar em ordem de prioridade
    crawlers = [
        ("Googlebot", {
            "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
            "Accept": "text/html,application/xhtml+xml",
        }),
        ("FacebookBot", {
            "User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)",
            "Accept": "text/html",
        }),
        ("WhatsApp", {
            "User-Agent": "WhatsApp/2.23.20.0",
            "Accept": "text/html",
        }),
    ]
    
    html = None
    for crawler_name, headers in crawlers:
        # Tentar até 2x com backoff para lidar com rate limiting (5xx)
        for attempt in range(2):
            try:
                r = requests.get(url, headers=headers, timeout=15)
                if r.status_code == 200:
                    if _is_shein_domain(domain):
                        if _is_shein_risk_page(r.url, r.text):
                            logging.info(f"[{domain}] {crawler_name} SSR: página de risco SHEIN detectada ({r.url}), tentando próximo...")
                            break

                        shein_images = _extract_shein_images_from_html(r.text, page_url=r.url)
                        if shein_images:
                            logging.info(f"[{domain}] {crawler_name} SSR: {len(shein_images)} imagens SHEIN encontradas!")
                            return shein_images

                        logging.info(f"[{domain}] {crawler_name} SSR: HTML SHEIN sem dados de produto, tentando próximo...")
                        break

                    # Verificar se o conteúdo tem dados úteis (não é só SPA shell)
                    cdn_pattern = r'(down-[a-z]+\.img\.susercontent\.com)/file/([a-zA-Z0-9_-]+)'
                    if re.search(cdn_pattern, r.text) or 'og:image' in r.text:
                        logging.info(f"[{domain}] {crawler_name} SSR: Conteúdo com imagens encontrado!")
                        html = r.text
                        break
                    else:
                        logging.info(f"[{domain}] {crawler_name} SSR: 200 mas sem imagens no HTML, tentando próximo...")
                        break  # 200 sem conteúdo = não adianta retry
                elif r.status_code >= 500 and attempt == 0:
                    import time
                    logging.info(f"[{domain}] {crawler_name} SSR: erro servidor ({r.status_code}), retry em 2s...")
                    time.sleep(2)
                    continue
                else:
                    logging.info(f"[{domain}] {crawler_name} SSR: bloqueado (status {r.status_code})")
                    break  # 4xx = não adianta retry
            except Exception as e:
                logging.info(f"[{domain}] {crawler_name} SSR erro: {e}")
                break
        if html:
            break
    
    if not html:
        logging.info(f"[{domain}] Todos os crawlers SSR falharam.")
        if 'shopee' in domain:
            return _extract_via_shopee_api(url, domain)
        return []
    
    # Detectar domínio de CDN de imagens (ex: down-br.img.susercontent.com)
    cdn_pattern = r'(down-[a-z]+\.img\.susercontent\.com)/file/([a-zA-Z0-9_-]+)'
    matches = re.findall(cdn_pattern, html)
    
    if not matches:
        logging.info(f"[{domain}] Crawler SSR: nenhum CDN de imagens encontrado no HTML.")
        if 'shopee' in domain:
            return _extract_via_shopee_api(url, domain)
        return []
    
    # Agrupar por CDN host e pegar hashes únicos
    cdn_host = matches[0][0]
    unique_hashes = list(set(h for _, h in matches))
    logging.info(f"[{domain}] Crawler SSR: {len(unique_hashes)} hashes únicos encontrados no CDN {cdn_host}")
    
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
    
    logging.info(f"[{domain}] Crawler SSR: {len(product_images)} imagens de produto (>= 200px) encontradas!")
    return product_images

def _extract_via_browser_api(url, domain, session):
    """
    Fallback para Shopee usando a sessão do BROWSER HEADLESS para acessar a API.
    Diferente do _extract_via_shopee_api (que usa requests puro e é bloqueado),
    essa função reutiliza a sessão Playwright/Camoufox que já passou pelo fingerprinting
    e possui cookies anti-crawler válidos (SPC_F, SPC_EC, etc).
    """
    # Extrair IDs do produto da URL
    match = re.search(r'/product/(\d+)/(\d+)', url)
    if not match:
        match = re.search(r'-i\.(\d+)\.(\d+)', url)
    
    if not match:
        logging.info(f"[{domain}] Browser API: Não foi possível extrair shop_id/item_id")
        return []
    
    shop_id = match.group(1)
    item_id = match.group(2)
    
    # Detectar CDN regional
    locale_map = {'br': 'br', 'co.id': 'id', 'sg': 'sg', 'com.my': 'my', 'co.th': 'th', 'vn': 'vn', 'ph': 'ph', 'tw': 'tw'}
    cdn_locale = 'br'  # default
    for suffix, loc in locale_map.items():
        if domain.endswith(suffix):
            cdn_locale = loc
            break
    cdn_host = f"down-{cdn_locale}.img.susercontent.com"
    
    api_url = f"https://{domain}/api/v4/pdp/get_pc?shop_id={shop_id}&item_id={item_id}"
    logging.info(f"[{domain}] Browser API: Buscando dados via sessão headless ({api_url})")
    
    try:
        api_page = session.fetch(api_url, network_idle=False)
        # O conteúdo da API é JSON renderizado como texto no body do browser
        import json
        body_text = api_page.css('body')[0].text if api_page.css('body') else ''
        # Às vezes o browser renderiza JSON dentro de um <pre> tag
        if not body_text:
            pre_tags = api_page.css('pre')
            if pre_tags:
                body_text = pre_tags[0].text
        
        if not body_text:
            logging.info(f"[{domain}] Browser API: Resposta vazia")
            return []
        
        data = json.loads(body_text)
        
        # Extrair imagens do JSON da API
        item_data = data.get('data', {})
        images = item_data.get('images', [])
        
        if images:
            product_images = [f"https://{cdn_host}/file/{h}" for h in images]
            logging.info(f"[{domain}] Browser API: {len(product_images)} imagens encontradas!")
            return product_images
        
        # Fallback: tentar extrair CDN URLs diretamente do texto JSON
        cdn_pattern = r'(down-[a-z]+\.img\.susercontent\.com)/file/([a-zA-Z0-9_-]+)'
        matches = re.findall(cdn_pattern, body_text)
        if matches:
            unique_hashes = list(set(h for _, h in matches))
            cdn_host_found = matches[0][0]
            product_images = [f"https://{cdn_host_found}/file/{h}" for h in unique_hashes]
            logging.info(f"[{domain}] Browser API (regex): {len(product_images)} imagens encontradas!")
            return product_images
        
        logging.info(f"[{domain}] Browser API: Nenhuma imagem no JSON (error={data.get('error', 'N/A')})")
    except json.JSONDecodeError as e:
        logging.info(f"[{domain}] Browser API: Resposta não é JSON válido: {e}")
        # Tentar extrair CDN URLs do HTML bruto (caso Shopee tenha retornado HTML)
        raw_html = str(api_page.html) if hasattr(api_page, 'html') else ''
        cdn_pattern = r'(down-[a-z]+\.img\.susercontent\.com)/file/([a-zA-Z0-9_-]+)'
        matches = re.findall(cdn_pattern, raw_html)
        if matches:
            unique_hashes = list(set(h for _, h in matches))
            product_images = [f"https://{matches[0][0]}/file/{h}" for h in unique_hashes]
            logging.info(f"[{domain}] Browser API (HTML fallback): {len(product_images)} imagens!")
            return product_images
    except Exception as e:
        logging.info(f"[{domain}] Browser API erro: {e}")
    
    return []

# Fashion product pages generally have large images in galleries.
# We will use Scrapling's StealthyFetcher to bypass possible basic bot protections.
def extract_product_images(url, session=None, wait_idle=False, escalation_level=1):
    url = resolve_short_url(url)
    domain = _domain_from_url(url)

    try:
        profile = database.get_profile(domain)
    except Exception as e:
        logging.error(f"Failed to fetch profile for {domain}: {e}")
        profile = {}

    requested_level = escalation_level or 1
    saved_level = profile.get('escalation_level', 1)
    base_level = max(requested_level, saved_level)
    base_wait_idle = wait_idle or profile.get('wait_idle', False) or base_level >= 2

    if saved_level > requested_level:
        logging.info(f"[{domain}] Memória carregada: Aplicando escalada automática para Nível {saved_level}.")
    elif profile.get('wait_idle', False) and not wait_idle:
        logging.info(f"[{domain}] Memória carregada: Redirecionando para busca JS/SPA pesada (wait_idle=True).")

    levels = _retry_escalation_levels(base_level, max_attempts=3)
    last_images = []

    for attempt, level in enumerate(levels, 1):
        attempt_wait_idle = base_wait_idle or level >= 2
        logging.info(f"[{domain}] Tentativa automática {attempt}/{len(levels)}: nível {level}, wait_idle={attempt_wait_idle}")

        images = _extract_product_images_once(
            url,
            session=session,
            wait_idle=attempt_wait_idle,
            escalation_level=level,
        )
        last_images = images
        logging.info(f"[{domain}] Tentativa automática {attempt}/{len(levels)} concluída: {len(images)} imagens")

        if images:
            if attempt > 1 and level > saved_level:
                logging.info(f"[{domain}] Auto-aprendizado: domínio vencido com nível {level}.")
                mark_escalation_required(domain, level)
            return images

    logging.info(f"[{domain}] Falha final: 0 imagens após {len(levels)} tentativas automáticas.")
    return last_images

def _extract_product_images_once(url, session=None, wait_idle=False, escalation_level=1):
    """
    Extracts product images from a fashion e-commerce URL using heuristics.
    """
    domain = _domain_from_url(url)

    if escalation_level >= 2:
        logging.info(f"[{domain}] Escalation Level {escalation_level}: Forçando hidratação pesada (wait_idle=True)")
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
            
        # Anti-Bot Wall Detection & Recovery (Login, Captcha, Traffic Verification)
        page_url_lower = page.url.lower()
        if 'login' in page_url_lower or 'signin' in page_url_lower or 'verify' in page_url_lower or 'captcha' in page_url_lower:
            logging.info(f"[{domain}] Anti-Bot/Login Wall Detectado! ({page.url})")

            if _is_shein_domain(domain):
                logging.info(f"[{domain}] Tentando fallback SHEIN dedicado antes do SSR genérico...")
                shein_html = str(page.html) if hasattr(page, 'html') else ''
                shein_images = _extract_via_shein(url, domain, session=session, html=shein_html, page_url=page.url)
                if shein_images:
                    images.update(shein_images)
                    return list(images)
                logging.info(f"[{domain}] Fallback SHEIN não retornou imagens. Tentando Googlebot SSR genérico...")
            
            # Estratégia 1: Para Shopee, usar a sessão do browser para acessar a API diretamente
            # O browser já tem cookies válidos e tokens anti-crawler do fingerprinting
            if 'shopee' in domain and session:
                logging.info(f"[{domain}] Tentando extração via Browser API (sessão headless com cookies)...")
                browser_images = _extract_via_browser_api(url, domain, session)
                if browser_images:
                    images.update(browser_images)
                    return list(images)
                logging.info(f"[{domain}] Browser API não retornou imagens. Tentando Googlebot SSR...")
            
            # Estratégia 2: Fallback via Googlebot SSR (funciona para sites que servem SSR ao Google)
            logging.info(f"[{domain}] Ativando fallback Googlebot SSR para extrair imagens do cache de SEO...")
            fallback_images = _extract_via_googlebot(url, domain)
            images.update(fallback_images)
            return list(images)

        if _is_shein_domain(domain):
            shein_html = str(page.html) if hasattr(page, 'html') else ''
            shein_images = _extract_via_shein(url, domain, session=session, html=shein_html, page_url=page.url)
            if shein_images:
                images.update(shein_images)
            
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
                logging.info(f"[{domain}] Smart Filter sem imagens similares; retry automatico deve tentar hidratacao JS.")
                return []
            
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
