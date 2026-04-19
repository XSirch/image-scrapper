from scrapling.fetchers import StealthyFetcher
url = 'https://www.carolbassi.com.br/vestido-irene-vinho-cassis-i470190104-2843/p'
page = StealthyFetcher.fetch(url, headless=True)
for img in page.css('img'):
    src = img.attrib.get('data-zoom-image') or img.attrib.get('data-large') or img.attrib.get('data-src') or img.attrib.get('src') or img.attrib.get('data-original')
    if src and 'I470' in src:
        print('SRC:', src)
