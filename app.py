from flask import Flask, render_template, request, jsonify
import cloudscraper
from bs4 import BeautifulSoup
import re, base64, json, time, random, urllib.parse
import datetime, threading
from cachetools import TTLCache

app = Flask(__name__)

# ============================================================
# THREAD-SAFE TTL CACHE (race condition fix)
# ============================================================
_cache      = TTLCache(maxsize=500, ttl=300)
_cache_lock = threading.Lock()

def cache_get(key):
    with _cache_lock:
        try:    return _cache[key]
        except: return None

def cache_set(key, value):
    with _cache_lock:
        _cache[key] = value

# ============================================================
# PER-REQUEST FRESH SCRAPER (no global sharing — main bug fix)
# ============================================================
def get_scraper():
    return cloudscraper.create_scraper(
        browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True}
    )

# ============================================================
# AUTO DOMAIN FETCHER (background thread)
# ============================================================
BASE_DOMAIN  = "https://new5.hdhub4u.fo"
_domain_lock = threading.Lock()

def _fetch_domain():
    global BASE_DOMAIN
    try:
        sc  = get_scraper()
        r   = sc.post("https://lite.duckduckgo.com/lite/", data={"q": "hdhub4u"}, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        for a in soup.find_all('a', class_='result-url'):
            href = a.get('href', '')
            if 'hdhub' in href.lower():
                m = re.match(r'(https?://[^/]+)', href)
                if m:
                    with _domain_lock:
                        BASE_DOMAIN = m.group(1)
                    print(f"[Domain] ✅ {BASE_DOMAIN}")
                    return
    except Exception as e:
        print(f"[Domain] ⚠️ {e}")

threading.Thread(target=_fetch_domain, daemon=True).start()

# ============================================================
# RETRY HELPER
# ============================================================
def with_retry(fn, times=3, delay=1.5):
    for i in range(times):
        try:
            result = fn()
            if result: return result
        except Exception as e:
            print(f"[Retry {i+1}/{times}] {e}")
            if i < times - 1:
                time.sleep(delay)
    return None

# ============================================================
# PRE-COMPILED REGEX
# ============================================================
TOKEN_RX        = re.compile(r"s\('o','([^']+)'")
HUBCLOUD_API_RX = re.compile(r"var\s+url\s*=\s*['\"]"
                              r"(https?://[^/]+/hubcloud\.php\?[^'\"]+)['\"]")
HUBCLOUD_DRV_RX = re.compile(r'(https?://hubcloud\.[a-z]+/drive/[a-z0-9]+)')
FINAL_PATTERNS  = [
    re.compile(r'(https?://[a-zA-Z0-9-]*googleusercontent\.com/[^\s\'"><]+)'),
    re.compile(r'(https?://[^\s\'"><]+\.(?:mkv|mp4|zip)(?:\?[^\s\'"><]*)?)'),
    re.compile(r'["\'](https?://[^"\']+\.mkv[^"\']*)["\']'),
]
BYPASS_DOMAINS  = ['gadgetsweb','cryptoinsights','hubdrive','hblinks','hubstream','hubcloud']

# ============================================================
# BYPASS CORE LOGIC (preserved exactly — your 48hr work)
# ============================================================
def rot13(s):
    return "".join(
        chr((ord(c)-65+13)%26+65) if 'A'<=c<='Z' else
        chr((ord(c)-97+13)%26+97) if 'a'<=c<='z' else c
        for c in s
    )

def get_final_url(session, url):
    domain  = urllib.parse.urlparse(url).netloc
    headers = {'Referer': f"https://{domain}/", 'X-Requested-With': 'XMLHttpRequest'}
    try:
        time.sleep(random.uniform(0.8, 1.4))   # reduced from 1.5-3s
        session.cookies.set('xyt', '2', domain=domain)
        r    = session.get(url, headers=headers, timeout=20)
        soup = BeautifulSoup(r.text, 'html.parser')
        form = soup.find('form')
        if form:
            data       = {i.get('name'): i.get('value','') for i in form.find_all('input') if i.get('name')}
            action_url = form.get('action') or url
            res        = session.post(action_url, data=data, headers=headers, timeout=20)
            html       = res.text
        else:
            html = r.text
        for p in FINAL_PATTERNS:
            m = p.search(html)
            if m: return m.group(1).replace('\\/', '/')
        return None
    except Exception as e:
        print(f"[get_final_url] {e}"); return None

def deep_bypass(url, session):
    try:
        if any(x in url for x in ['cryptoinsights','gadgetsweb']):
            session.cookies.set('xla', 's4t', domain='cryptoinsights.site')
            r = session.get(url, timeout=15)
            m = TOKEN_RX.search(r.text)
            if m:
                d_step   = rot13(base64.b64decode(base64.b64decode(m.group(1)).decode()).decode())
                next_url = base64.b64decode(json.loads(base64.b64decode(d_step).decode())['o']).decode()
                return deep_bypass(next_url, session)

        if any(x in url for x in ['hblinks','hubdrive','hubcloud']):
            r = session.get(url, timeout=15)
            am = HUBCLOUD_API_RX.search(r.text)
            if am: return get_final_url(session, am.group(1))
            dm = HUBCLOUD_DRV_RX.search(r.text)
            if dm: return deep_bypass(dm.group(1), session)

        return get_final_url(session, url)
    except Exception as e:
        print(f"[deep_bypass] {e}"); return None

def extract_qualities(url):
    def _try():
        sc   = get_scraper()
        r    = sc.get(url, timeout=15)
        if r.status_code in [403,503] or "Just a moment" in r.text:
            return None
        soup = BeautifulSoup(r.text, 'html.parser')
        out  = []
        for a in soup.find_all('a', href=True):
            txt = a.text.lower()
            if any(q in txt for q in ['480p','720p','1080p','2160p','4k']):
                if any(d in a['href'] for d in BYPASS_DOMAINS):
                    if not any(x['link'] == a['href'] for x in out):
                        out.append({'name': a.text.strip(), 'link': a['href']})
        return out if out else None

    return with_retry(_try, times=3, delay=1.5) or []

# ============================================================
# ROUTES
# ============================================================

# SPA catch-all — fixes refresh/back bug
@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def serve(path):
    # Let API routes through
    if path.startswith('api/') or path == 'ping':
        return "Not found", 404
    return render_template('index.html')

@app.route('/ping')
def ping():
    return 'ok', 200

@app.route('/api/home')
def home_api():
    category = request.args.get('category', '').strip()
    page     = max(1, int(request.args.get('page', 1)))
    ck       = f"home_{category}_{page}"
    cached   = cache_get(ck)
    if cached: return jsonify({'status':'success','data':cached})

    CAT_MAP     = {
        'Bollywood':'BollyWood','Hollywood':'HollyWood',
        'South':'South Indian','Web Series':'WEB-Series',
        'Netflix':'Netflix','Amazon':'Amazon Prime Video',
    }
    SEARCH_TABS = {'South','Web Series'}
    api_cat     = CAT_MAP.get(category, category)
    use_search  = category in SEARCH_TABS

    params = {
        'q'                  : api_cat if use_search else '*',
        'query_by'           : 'post_title,category,stars,director,imdb_id',
        'query_by_weights'   : '4,2,2,2,4',
        'sort_by'            : 'sort_by_date:desc',
        'limit'              : '24',
        'highlight_fields'   : 'none',
        'use_cache'          : 'true',
        'page'               : str(page),
        'analytics_tag'      : datetime.datetime.now().strftime('%Y-%m-%d'),
    }
    if not use_search and api_cat:
        params['filter_by'] = f"category:={api_cat}"

    with _domain_lock: domain = BASE_DOMAIN

    try:
        sc  = get_scraper()
        r   = sc.get("https://search.pingora.fyi/collections/post/documents/search",
                     params=params, headers={'Referer':f"{domain}/"}, timeout=15)
        raw = r.json()
        results = [
            {
                'title'   : d.get('post_title','Unknown'),
                'url'     : d.get('permalink'),
                'poster'  : d.get('post_thumbnail') or '',
                'category': d.get('category',''),
                'year'    : d.get('year',''),
                'imdb'    : d.get('stars',''),
                'language': d.get('language',''),
            }
            for hit in raw.get('hits',[])
            if (d:=hit.get('document',{})) and d.get('permalink')
        ]
        cache_set(ck, results)
        return jsonify({'status':'success','data':results})
    except Exception as e:
        return jsonify({'status':'error','msg':str(e)}), 500

@app.route('/api/search', methods=['POST'])
def search_api():
    query = (request.json or {}).get('query','').strip()
    if not query: return jsonify({'status':'error','msg':'Empty query'}), 400

    ck     = f"search_{query.lower()}"
    cached = cache_get(ck)
    if cached: return jsonify({'status':'success','data':cached})

    params = {
        'q'               : query,
        'query_by'        : 'post_title,category,stars,director,imdb_id',
        'query_by_weights': '4,2,2,2,4',
        'sort_by'         : 'sort_by_date:desc',
        'limit'           : '20',
        'highlight_fields': 'none',
        'use_cache'       : 'true',
        'page'            : '1',
        'analytics_tag'   : datetime.datetime.now().strftime('%Y-%m-%d'),
    }
    with _domain_lock: domain = BASE_DOMAIN
    try:
        sc  = get_scraper()
        r   = sc.get("https://search.pingora.fyi/collections/post/documents/search",
                     params=params, headers={'Referer':f"{domain}/"}, timeout=15)
        raw = r.json()
        results = [
            {
                'title'   : d.get('post_title','Unknown'),
                'url'     : d.get('permalink'),
                'poster'  : d.get('post_thumbnail') or '',
                'category': d.get('category',''),
                'year'    : d.get('year',''),
                'imdb'    : d.get('stars',''),
                'language': d.get('language',''),
            }
            for hit in raw.get('hits',[])
            if (d:=hit.get('document',{})) and d.get('permalink')
        ]
        cache_set(ck, results)
        return jsonify({'status':'success','data':results})
    except Exception as e:
        return jsonify({'status':'error','msg':str(e)}), 500

@app.route('/api/qualities', methods=['POST'])
def get_qualities():
    movie_url = (request.json or {}).get('url','').strip()
    if not movie_url: return jsonify({'status':'error','msg':'No URL'}), 400
    if movie_url.startswith('/'):
        with _domain_lock: movie_url = f"{BASE_DOMAIN}{movie_url}"

    ck     = f"qual_{movie_url}"
    cached = cache_get(ck)
    if cached: return jsonify({'status':'success','data':cached})

    qualities = extract_qualities(movie_url)
    if qualities:
        cache_set(ck, qualities)
        return jsonify({'status':'success','data':qualities})
    return jsonify({'status':'error','msg':'No download links found. Please try again.'}), 404

@app.route('/api/bypass', methods=['POST'])
def run_bypass():
    target = (request.json or {}).get('url','').strip()
    if not target: return jsonify({'status':'error','msg':'No URL'}), 400

    def _do():
        session = get_scraper()
        parsed  = urllib.parse.urlparse(target)
        session.cookies.set('xyt','1', domain=parsed.netloc)
        return deep_bypass(target, session)

    result = with_retry(_do, times=3, delay=2)
    if result:
        return jsonify({'status':'success','download_url':result})
    return jsonify({'status':'error','msg':'Could not extract link. Please try again.'}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000, debug=False)