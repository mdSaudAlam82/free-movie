from flask import Flask, render_template, request, jsonify
import cloudscraper
from bs4 import BeautifulSoup
import re, base64, json, time, random, urllib.parse
import datetime
import threading
import os

app = Flask(__name__)

# ==========================================
# ⚙️ GLOBAL CONFIG & PROXY SETUP
# ==========================================

# Render Dashboard mein PROXY_URL env variable set karo
# Format: http://username:password@proxy.webshare.io:80
PROXY_URL = os.environ.get("PROXY_URL")
proxies = {"http": PROXY_URL, "https": PROXY_URL} if PROXY_URL else None

if proxies:
    masked = PROXY_URL.split("@")[-1] if "@" in PROXY_URL else "set"
    print(f"[DEBUG] 🛡️ Proxy ACTIVE: {masked}")
else:
    print("[DEBUG] ⚠️ No proxy — running on direct IP")

scraper = cloudscraper.create_scraper(
    browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
)
if proxies:
    scraper.proxies.update(proxies)

# Cache system — simple dict with timestamp
_cache = {}
CACHE_TTL = 300  # 5 minutes

def cache_get(key):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return data
        del _cache[key]
    return None

def cache_set(key, value):
    _cache[key] = (value, time.time())

# Domain starts with a safe fallback immediately
BASE_DOMAIN = "https://new5.hdhub4u.fo"
_domain_lock = threading.Lock()

def get_latest_domain():
    print("\n[DEBUG] 🔄 Searching internet for the latest HDHub4u domain...")
    try:
        r = scraper.post(
            "https://lite.duckduckgo.com/lite/",
            data={"q": "hdhub4u"},
            timeout=10
        )
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all('a', class_='result-url'):
            href = a.get('href', '')
            if 'hdhub' in href.lower():
                match = re.match(r'(https?://[^/]+)', href)
                if match:
                    latest = match.group(1)
                    print(f"[DEBUG] ✅ Latest domain Auto-Fetched: {latest}\n")
                    return latest
    except Exception as e:
        print(f"[DEBUG] ⚠️ Auto-fetch failed: {e}")

    print("[DEBUG] ⚠️ Using default fallback domain.\n")
    return "https://new5.hdhub4u.fo"

def _domain_fetcher_thread():
    global BASE_DOMAIN
    domain = get_latest_domain()
    with _domain_lock:
        BASE_DOMAIN = domain

# 🚀 Non-blocking startup — Flask starts instantly, domain fetches in background
threading.Thread(target=_domain_fetcher_thread, daemon=True).start()

# ==========================================
# 🔧 PRE-COMPILED REGEX PATTERNS
# ==========================================
TOKEN_MATCH_REGEX    = re.compile(r"s\('o','([^']+)'")
HUBCLOUD_API_REGEX   = re.compile(r"var\s+url\s*=\s*['\"](https?://[^/]+/hubcloud\.php\?[^'\"]+)['\"]")
HUBCLOUD_DRIVE_REGEX = re.compile(r'(https?://hubcloud\.[a-z]+/drive/[a-z0-9]+)')
FINAL_URL_PATTERNS   = [
    re.compile(r'(https?://[a-zA-Z0-9-]*googleusercontent\.com/[^\s\'"><]+)'),
    re.compile(r'(https?://[^\s\'"><]+\.(?:mkv|mp4|zip)(?:\?[^\s\'"><]*)?)'),
    re.compile(r'["\'](https?://[^"\']+\.mkv[^"\']*)["\']'),
]

# ==========================================
# 🛡️ PHASE 1: CORE BYPASS LOGIC
# (Ek line bhi nahi badli — tumhara 48hr ka kaam safe hai)
# ==========================================
def rot13(s):
    return "".join(
        [chr((ord(c) - 65 + 13) % 26 + 65) if 'A' <= c <= 'Z'
         else chr((ord(c) - 97 + 13) % 26 + 97) if 'a' <= c <= 'z'
         else c for c in s]
    )

def get_final_url(session, url):
    domain = urllib.parse.urlparse(url).netloc
    headers = {
        'Referer': f"https://{domain}/",
        'X-Requested-With': 'XMLHttpRequest'
    }
    try:
        time.sleep(random.uniform(1.5, 3))
        session.cookies.set('xyt', '2', domain=domain)

        r = session.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')

        form = soup.find('form')
        if form:
            data = {
                inp.get('name'): inp.get('value', '')
                for inp in form.find_all('input') if inp.get('name')
            }
            action_url = form.get('action') or url
            res = session.post(action_url, data=data, headers=headers, timeout=20)
            html_content = res.text
        else:
            html_content = r.text

        for p in FINAL_URL_PATTERNS:
            match = p.search(html_content)
            if match:
                return match.group(1).replace('\\/', '/')
        return url
    except Exception as e:
        print(f"[DEBUG] get_final_url Error: {str(e)}")
        return f"Error: {str(e)}"

def deep_bypass(url, session):
    try:
        if any(x in url for x in ['cryptoinsights', 'gadgetsweb']):
            session.cookies.set('xla', 's4t', domain='cryptoinsights.site')
            r = session.get(url, timeout=15)
            token_match = TOKEN_MATCH_REGEX.search(r.text)
            if token_match:
                d_step = rot13(
                    base64.b64decode(
                        base64.b64decode(token_match.group(1)).decode('utf-8')
                    ).decode('utf-8')
                )
                next_url = base64.b64decode(
                    json.loads(base64.b64decode(d_step).decode('utf-8'))['o']
                ).decode('utf-8')
                return deep_bypass(next_url, session)

        if any(x in url for x in ['hblinks', 'hubdrive', 'hubcloud']):
            r = session.get(url, timeout=15)
            api_match = HUBCLOUD_API_REGEX.search(r.text)
            if api_match:
                return get_final_url(session, api_match.group(1))
            drive_match = HUBCLOUD_DRIVE_REGEX.search(r.text)
            if drive_match:
                return deep_bypass(drive_match.group(1), session)

        return get_final_url(session, url)
    except Exception as e:
        print(f"[DEBUG] deep_bypass Error on {url}: {str(e)}")
        return url

# ==========================================
# 🕸️ PHASE 2: HTML SCRAPER
# (Original logic — untouched)
# ==========================================
def extract_qualities(url):
    print(f"\n[DEBUG] 🔍 Scrape request: {url}")
    if not url:
        return []
    try:
        r = scraper.get(url, timeout=15)
        if r.status_code in [403, 503] or "Just a moment" in r.text or "Cloudflare" in r.text:
            print("[DEBUG] 🚨 ERROR: Blocked by Cloudflare!")
            return []

        soup = BeautifulSoup(r.text, 'html.parser')
        qualities = []
        for a in soup.find_all('a', href=True):
            txt = a.text.lower()
            if any(q in txt for q in ['480p', '720p', '1080p', '2160p']):
                if any(d in a['href'] for d in ['gadgetsweb', 'cryptoinsights', 'hubdrive', 'hblinks', 'hubstream']):
                    if not any(q['link'] == a['href'] for q in qualities):
                        qualities.append({'name': a.text.strip(), 'link': a['href']})
        return qualities
    except Exception as e:
        print(f"[DEBUG] 💥 Extraction Error: {e}")
        return []

# ==========================================
# 🌐 PHASE 3: FLASK ROUTES
# ==========================================
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/home', methods=['GET'])
def home_api():
    """
    Home screen ke liye latest movies — same secret API, zero extra scraping risk.
    Client-side category filter bhi support karta hai ?category=Bollywood
    """
    category = request.args.get('category', '').strip()
    cache_key = f"home_{category}"

    cached = cache_get(cache_key)
    if cached:
        print(f"[DEBUG] ⚡ Cache hit: {cache_key}")
        return jsonify({'status': 'success', 'data': cached})

    api_url = "https://search.pingora.fyi/collections/post/documents/search"

    # UI tab name → exact API category name mapping
    CATEGORY_MAP = {
        'Bollywood':  'BollyWood',
        'Hollywood':  'HollyWood',
        'South':      'South Indian',
        'Web Series': 'WEB-Series',
        'Netflix':    'Netflix',
        'Amazon':     'Amazon Prime Video',
    }

    # Search-based tabs — filter_by ki jagah q use karo
    SEARCH_TABS = {'South', 'Web Series'}

    api_category = CATEGORY_MAP.get(category, category)
    use_search   = category in SEARCH_TABS

    if use_search:
        # South aur Web Series ke liye full-text search better hai
        query = api_category
        params = {
            'q': query,
            'query_by': 'post_title,category,stars,director,imdb_id',
            'query_by_weights': '4,2,2,2,4',
            'sort_by': 'sort_by_date:desc',
            'limit': '24',
            'highlight_fields': 'none',
            'use_cache': 'true',
            'page': '1',
            'analytics_tag': datetime.datetime.now().strftime('%Y-%m-%d'),
        }
    else:
        query = '*'
        params = {
            'q': query,
            'query_by': 'post_title,category,stars,director,imdb_id',
            'query_by_weights': '4,2,2,2,4',
            'sort_by': 'sort_by_date:desc',
            'limit': '24',
            'highlight_fields': 'none',
            'use_cache': 'true',
            'page': '1',
            'analytics_tag': datetime.datetime.now().strftime('%Y-%m-%d'),
        }
        if api_category:
            params['filter_by'] = f"category:={api_category}"

    with _domain_lock:
        current_domain = BASE_DOMAIN

    headers = {'Referer': f"{current_domain}/"}

    try:
        r = scraper.get(api_url, params=params, headers=headers, timeout=15)
        data = r.json()
        results = []
        if 'hits' in data:
            for hit in data['hits']:
                doc = hit.get('document', {})
                if doc.get('permalink'):
                    results.append({
                        'title': doc.get('post_title', 'Unknown'),
                        'url': doc.get('permalink'),
                        'poster': doc.get('post_thumbnail') or '',
                        'category': doc.get('category', ''),
                        'year': doc.get('year', ''),
                        'imdb': doc.get('stars', ''),
                        'language': doc.get('language', ''),
                    })

        cache_set(cache_key, results)
        return jsonify({'status': 'success', 'data': results})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/search', methods=['POST'])
def search_api():
    query = request.json.get('query', '').strip()
    if not query:
        return jsonify({'status': 'error', 'msg': 'Empty query'}), 400

    # Cache check
    cache_key = f"search_{query.lower()}"
    cached = cache_get(cache_key)
    if cached:
        print(f"[DEBUG] ⚡ Cache hit: {cache_key}")
        return jsonify({'status': 'success', 'data': cached})

    api_url = "https://search.pingora.fyi/collections/post/documents/search"
    params = {
        'q': query,
        'query_by': 'post_title,category,stars,director,imdb_id',
        'query_by_weights': '4,2,2,2,4',
        'sort_by': 'sort_by_date:desc',
        'limit': '15',
        'highlight_fields': 'none',
        'use_cache': 'true',
        'page': '1',
        'analytics_tag': datetime.datetime.now().strftime('%Y-%m-%d'),
    }

    with _domain_lock:
        current_domain = BASE_DOMAIN

    headers = {'Referer': f"{current_domain}/"}

    try:
        r = scraper.get(api_url, params=params, headers=headers, timeout=15)
        data = r.json()
        results = []
        if 'hits' in data:
            for hit in data['hits']:
                doc = hit.get('document', {})
                if doc.get('permalink'):
                    results.append({
                        'title': doc.get('post_title', 'Unknown'),
                        'url': doc.get('permalink'),
                        'poster': doc.get('post_thumbnail') or '',
                        'category': doc.get('category', ''),
                        'year': doc.get('year', ''),
                        'imdb': doc.get('stars', ''),
                        'language': doc.get('language', ''),
                    })

        cache_set(cache_key, results)
        return jsonify({'status': 'success', 'data': results})
    except Exception as e:
        return jsonify({'status': 'error', 'msg': str(e)}), 500


@app.route('/api/qualities', methods=['POST'])
def get_qualities():
    movie_url = request.json.get('url', '').strip()
    if movie_url.startswith('/'):
        with _domain_lock:
            movie_url = f"{BASE_DOMAIN}{movie_url}"

    qualities = extract_qualities(movie_url)
    if qualities:
        return jsonify({'status': 'success', 'data': qualities})
    return jsonify({'status': 'error', 'msg': 'No download links found on this page.'})


@app.route('/api/bypass', methods=['POST'])
def run_bypass():
    target_url = request.json.get('url')
    final_session = cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )
    # Proxy bypass session pe bhi lagao
    if proxies:
        final_session.proxies.update(proxies)
    final_session.cookies.set('xyt', '1', domain='hubdrive.space')
    final_link = deep_bypass(target_url, final_session)
    return jsonify({'status': 'success', 'download_url': final_link})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=False)