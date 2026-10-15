#!/usr/bin/env python3
"""Bouw statische blogpagina's vanuit Pages CMS. Geen externe pakketten nodig.

Gebruik voor live Cloudflare Pages (later, na goedkeuring):
  python3 build_site.py
  outputmap: dist
Voor een lokale, niet-geïndexeerde proefversie:
  python3 build_site.py --preview
"""
import argparse
import html
from html.parser import HTMLParser
import json
import re
import shutil
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DOMAIN = 'https://polaircontext.nl'
CATS = {
    'hoofd': 'In mijn hoofd',
    'voordeur': 'Achter de voordeur',
    'mensen': 'Tussen de mensen',
    'pillen': 'Pillen & praten',
    'toch': 'En toch...',
    'uitleg': 'Hoe zit dat nou?',
}


def slugify(text):
    text = unicodedata.normalize('NFD', text.lower())
    text = ''.join(ch for ch in text if unicodedata.category(ch) != 'Mn')
    return re.sub(r'(^-+|-+$)', '', re.sub(r'[^a-z0-9]+', '-', text))


class AllowlistHTML(HTMLParser):
    """Behoud alleen veilige, eenvoudige opmaak uit de CMS-blogtekst."""
    ALLOWED = {'p', 'strong', 'em', 'b', 'i', 'u', 'blockquote', 'ul', 'ol', 'li', 'h2', 'h3', 'a', 'br'}
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.stack = []
        self.suppress = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style', 'iframe'):
            self.suppress += 1
            self.stack.append(None)
            return
        if self.suppress:
            self.stack.append(None)
            return
        if tag not in self.ALLOWED:
            self.stack.append(None)
            return
        if tag == 'a':
            raw_href = dict(attrs).get('href') or ''
            if raw_href.startswith(('https://', 'http://', 'mailto:')):
                self.parts.append(f'<a href="{html.escape(raw_href, quote=True)}" rel="noopener noreferrer">')
            else:
                self.parts.append('<a>')
        elif tag == 'br':
            self.parts.append('<br>')
            self.stack.append(None)
            return
        else:
            self.parts.append(f'<{tag}>')
        self.stack.append(tag)

    def handle_endtag(self, tag):
        # Rich text from Pages CMS has properly nested tags; for malformed
        # HTML we do not emit any unknown closing tag.
        if self.stack:
            top = self.stack.pop()
            if top is not None:
                self.parts.append(f'</{top}>')
        if tag in ('script', 'style', 'iframe') and self.suppress:
            self.suppress -= 1

    def handle_data(self, data):
        if not self.suppress:
            self.parts.append(html.escape(data))

    def get_html(self):
        while self.stack:
            top = self.stack.pop()
            if top:
                self.parts.append(f'</{top}>')
        return ''.join(self.parts)


def safe_body(raw):
    parser = AllowlistHTML()
    parser.feed(raw)
    return parser.get_html()


def escaped(text):
    return html.escape(str(text), quote=True)


def make_home(html_source, preview):
    # De verandering is bewust klein: de knop wordt een echte link.
    old = "const b=document.createElement('button');b.className='read';b.textContent='Lees verder →';b.onclick=()=>openModal(p.label,p.title,p.body,p.sources);"
    new = ("const b=document.createElement('a');b.className='read';"
           "b.href=articleHref(p.title);b.textContent='Lees verder →';"
           "b.onclick=e=>{if(e.ctrlKey||e.metaKey||e.shiftKey||e.altKey)return;"
           "e.preventDefault();openBlog(p)};")
    if html_source.count(old) != 1:
        raise RuntimeError('Homepage gewijzigd: verwachte Lees-verder-code niet precies eenmaal gevonden.')
    html_source = html_source.replace(old, new)
    needle = 'const posts=document.getElementById(\'posts\');'
    replacement = ("function slugify(text){return text.toLowerCase().normalize('NFD')"
                   ".replace(/[\\u0300-\\u036f]/g,'').replace(/[^a-z0-9]+/g,'-')"
                   ".replace(/^-+|-+$/g,'')}"
                   "function articleHref(title){const url='blog/'+slugify(title)+'/';"
                   "return location.protocol==='file:'?url:'/'+url}" + needle)
    if html_source.count(needle) != 1:
        raise RuntimeError('Kan blogweergave niet uniek vinden.')
    html_source = html_source.replace(needle, replacement)
    needle = "const modal=document.getElementById('modal');"
    replacement = ("let hasBlogOverlay=false;"
                   "function openBlog(p){openModal(p.label,p.title,p.body,p.sources);"
                   "const a=document.createElement('p');a.style.marginTop='27px';"
                   "a.style.fontSize='14px';const l=document.createElement('a');"
                   "l.href=articleHref(p.title);l.textContent='Eigen link naar deze blog ↗';"
                   "l.style.color='var(--deep)';a.append(l);"
                   "document.getElementById('modalbody').append(a);"
                   "if(location.protocol==='file:')return;
                   "}"
                   +needle)
    if html_source.count(needle) != 1:
        raise RuntimeError('Kan de bestaande blog-pop-up niet uniek vinden.')
    html_source = html_source.replace(needle, replacement)
    needle = "modal.addEventListener('click',e=>{if(e.target===modal)modal.close()});"
    addition = ("modal.addEventListener('close',()=>{if(hasBlogOverlay){hasBlogOverlay=false;"
                "if(history.state&&history.state.polairBlog)history.back()}});"
                "window.addEventListener('popstate',()=>{if(hasBlogOverlay&&modal.open){"
                "hasBlogOverlay=false;modal.close()}});")
    if html_source.count(needle) != 1:
        raise RuntimeError('Kan de sluitactie van de bestaande pop-up niet uniek vinden.')
    html_source = html_source.replace(needle, needle+addition)
    # CSS: anker heeft nog steeds exact dezelfde Lees verder-opmaak als button.
    html_source = html_source.replace(
        '</style>', '.read{display:inline-block;text-decoration:none;cursor:pointer}\n</style>', 1)
    # Links naar statische blogs blijven beschikbaar voor zoekmachines.
    html_source = html_source.replace('</head>',
        '<link rel="canonical" href="https://polaircontext.nl/">'
        + ('<meta name="robots" content="noindex,nofollow">' if preview else '')
        + '</head>', 1)
    return html_source


def make_blog_page(row, slug, common_css, brand_header, preview, analytics_fragment):
    title = escaped(row['title'])
    category = escaped(CATS.get(row['cat'], row['cat']))
    summary = escaped(row.get('summary',''))
    page_url = f'{DOMAIN}/blog/{slug}/'
    raw_body = safe_body(row.get('body', ''))
    src = []
    for item in row.get('sources', []) or []:
        url = str(item.get('url') or '')
        label = str(item.get('label') or '')
        if url.startswith(('https://','http://')) and label:
            src.append(f'<li><a href="{escaped(url)}" target="_blank" rel="noopener noreferrer">{escaped(label)}</a></li>')
    sources = ('<section class="sources"><h2>Verder lezen</h2><ul>' + ''.join(src) + '</ul></section>') if src else ''
    robots = '<meta name="robots" content="noindex,nofollow">' if preview else ''
    back_link = '../../index.html#blogs' if preview else '../../#blogs'
    article_css = '''
    .article-wrap{width:min(720px,calc(100% - 28px));margin:40px auto 80px;
       border:1px solid var(--line);border-radius:18px;box-shadow:0 12px 38px #251a1715;
       background:white;padding:36px 41px}
    .article-wrap h1{font-size:clamp(27px,4.4vw,38px);font-weight:800;line-height:1.2;margin:9px 0 24px;letter-spacing:-.03em}
    .article-copy p{margin:0 0 1.15em;line-height:1.8;color:var(--ink)}
    .article-copy a,.sources a,.back a{color:var(--deep);text-decoration:underline}
    .article-copy ul,.article-copy ol{padding-left:1.3em}
    .article-copy h2,.sources h2{font-size:19px;margin:24px 0 10px}
    .article-wrap .cat{margin:0 0 8px}
    .back{margin:34px 0 0;padding-top:24px;border-top:1px solid var(--line)}
    .reader-footer{background:#fff3e8;padding:24px 18px;text-align:center;color:var(--muted);font-size:13px}
    @media(max-width:600px){.article-wrap{margin:17px auto 46px;padding:25px 22px}.article-wrap h1{font-size:29px}}
    '''
    # Het boek en de overige inhoud blijven op de gewone homepage;
    # deze artikelpagina is bewust rustig en lijkt op de bestaande lees-pop-up.
    return f'''<!doctype html>
<html lang="nl"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f85c0c">
<title>{title} | Polaircontext</title>
<meta name="description" content="{summary}">
<link rel="canonical" href="{page_url}">{robots}
<meta property="og:type" content="article">
<meta property="og:site_name" content="Polaircontext">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{summary}">
<meta property="og:url" content="{page_url}">
<meta property="og:image" content="{DOMAIN}/polaircontext-deelafbeelding.jpg">
<meta name="twitter:card" content="summary_large_image">
<style>{common_css}\n{article_css}</style></head><body>
{brand_header}
<main id="artikel"><article class="article-wrap">
<div class="cat">{category}</div>
<h1>{title}</h1>
<div class="article-copy">{raw_body}</div>
{sources}
<p class="back"><a href="{back_link}">← Terug naar de blogs</a></p>
</article></main>
<footer class="reader-footer">Polaircontext · Bipolair: niet mijn keuze, wel mijn leven.</footer>
{analytics_fragment}</body></html>'''


def build(preview):
    html_source = (ROOT/'index.html').read_text(encoding='utf-8')
    analytics_match = re.search(r'<!-- Cloudflare Web Analytics -->.*?<!-- End Cloudflare Web Analytics -->', html_source, flags=re.S)
    analytics_fragment = analytics_match.group() if analytics_match else ''
    rows = json.loads((ROOT/'content/blogs.json').read_text(encoding='utf-8'))
    assert isinstance(rows, list)
    out = ROOT/'dist'
    if out.exists():
        shutil.rmtree(out)
    out.mkdir()
    (out/'content').mkdir()
    shutil.copy2(ROOT/'content/blogs.json', out/'content/blogs.json')
    shutil.copy2(ROOT/'polaircontext-deelafbeelding.jpg', out/'polaircontext-deelafbeelding.jpg')
    (out/'index.html').write_text(make_home(html_source, preview), encoding='utf-8')
    css_match = re.search(r'<style>(.*?)</style>', html_source, flags=re.S)
    nav_match = re.search(r'<header class="nav">.*?</header>', html_source, flags=re.S)
    if not css_match or not nav_match:
        raise RuntimeError('Kan de bestaande vormgeving niet uitlezen.')
    header = nav_match.group().replace('href="#home"', 'href="../../"').replace('href="#blogs"', 'href="../../#blogs"').replace('href="#kwijt"', 'href="../../#kwijt"')
    urls = [DOMAIN+'/']
    slugs = set()
    for row in rows:
        if not row.get('title') or not row.get('cat'):
            continue
        slug = slugify(row['title'])
        if not slug or slug in slugs:
            raise RuntimeError('Dubbele of ongeldige blogtitel bij het maken van bloglinks: '+row['title'])
        slugs.add(slug)
        folder = out/'blog'/slug
        folder.mkdir(parents=True)
        (folder/'index.html').write_text(make_blog_page(row, slug, css_match.group(1), header, preview, analytics_fragment), encoding='utf-8')
        urls.append(f'{DOMAIN}/blog/{slug}/')
    (out/'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        +''.join('  <url><loc>'+escaped(url)+'</loc></url>\n' for url in urls)
        +'</urlset>\n', encoding='utf-8')
    if preview:
        (out/'robots.txt').write_text('User-agent: *\nDisallow: /\n',encoding='utf-8')
    else:
        (out/'robots.txt').write_text('User-agent: *\nAllow: /\nSitemap: '+DOMAIN+'/sitemap.xml\n', encoding='utf-8')
    print('Gemaakt:',len(slugs),'blogpagina’s in',out, '(VEILIGE PROEFVERSIE)' if preview else '(productie-uitvoer)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true', help='Maak testbestanden met noindex')
    build(parser.parse_args().preview)
