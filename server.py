"""Mini meta search engine: web / images / videos (playable inline).
Run: python server.py  ->  http://localhost:8765
No API keys, stdlib only.
"""
import base64
import hmac
import json
import os
import re
import traceback
import urllib.parse
import urllib.request
from html import unescape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
HERE = Path(__file__).parent


def fetch(url, data=None, headers=None):
    h = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"}
    h.update(headers or {})
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read().decode("utf-8", "replace")


def strip_tags(s):
    return unescape(re.sub(r"<[^>]+>", "", s)).strip()


def search_web(q, page=0):
    # DDG blocks datacenter IPs (Render) with a challenge page -> fall back to Bing.
    try:
        out = search_web_ddg(q, page)
    except Exception:
        traceback.print_exc()
        out = []
    return out or search_web_bing(q, page)


def search_web_bing(q, page=0):
    html = fetch("https://www.bing.com/search?setlang=ja&cc=JP&q=" + urllib.parse.quote(q)
                 + f"&first={page * 10 + 1}")
    out = []
    for b in re.split(r'<li class="b_algo', html)[1:]:
        m = re.search(r'<h2[^>]*>\s*<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        url = unescape(m.group(1))
        u = urllib.parse.parse_qs(urllib.parse.urlparse(url).query).get("u")
        if "bing.com/ck/" in url and u:  # tracking redirect: u=a1<base64url>
            try:
                s = u[0][2:]
                url = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)).decode()
            except Exception:
                pass
        sn = re.search(r'<p[^>]*>(.*?)</p>', b, re.S)
        out.append({"title": strip_tags(m.group(2)), "url": url,
                    "snippet": strip_tags(sn.group(1)) if sn else ""})
    return out


def search_web_ddg(q, page=0):
    html = fetch("https://html.duckduckgo.com/html/",
                 {"q": q, "kl": "jp-jp", "s": str(page * 30)})
    out = []
    blocks = re.split(r'<div class="result results_links', html)[1:]
    for b in blocks:
        if "result--ad" in b[:200]:
            continue
        m = re.search(r'class="result__a" href="([^"]+)"[^>]*>(.*?)</a>', b, re.S)
        if not m:
            continue
        href = unescape(m.group(1))
        u = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg")
        url = u[0] if u else href
        sn = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', b, re.S)
        out.append({"title": strip_tags(m.group(2)), "url": url,
                    "snippet": strip_tags(sn.group(1)) if sn else ""})
    return out


def _walk(o, key):
    if isinstance(o, dict):
        if key in o:
            yield o[key]
        for v in o.values():
            yield from _walk(v, key)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v, key)


def search_videos(q):
    html = fetch("https://www.youtube.com/results?hl=ja&search_query="
                 + urllib.parse.quote(q))
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        return []
    data = json.loads(m.group(1))
    out = []
    for v in _walk(data, "videoRenderer"):
        vid = v.get("videoId")
        if not vid:
            continue
        txt = lambda k: (v.get(k) or {}).get("simpleText") or "".join(
            r.get("text", "") for r in (v.get(k) or {}).get("runs", []))
        out.append({"id": vid, "title": txt("title"), "channel": txt("ownerText"),
                    "length": txt("lengthText"), "views": txt("viewCountText"),
                    "published": txt("publishedTimeText"),
                    "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", "src": "yt"})
    return out


def _dur(sec):
    try:
        s = int(float(sec))
    except (TypeError, ValueError):
        return ""
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def search_commons(q):
    """Wikimedia Commons: freely licensed video files, playable directly in <video>."""
    d = json.loads(fetch(
        "https://commons.wikimedia.org/w/api.php?action=query&format=json&generator=search"
        "&gsrnamespace=6&gsrlimit=12&prop=imageinfo&iiprop=url|metadata|user&iiurlwidth=480"
        "&gsrsearch=" + urllib.parse.quote("filetype:video " + q)))
    pages = sorted((d.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    out = []
    for p in pages:
        ii = (p.get("imageinfo") or [{}])[0]
        if not ii.get("url"):
            continue
        meta = {m.get("name"): m.get("value") for m in ii.get("metadata") or []}
        title = re.sub(r"^File:|\.(webm|ogv|ogg|mpg|mpeg)$", "", p.get("title", ""), flags=re.I)
        out.append({"id": ii["url"], "file": ii["url"], "title": title,
                    "channel": ii.get("user", ""), "length": _dur(meta.get("playtime_seconds")),
                    "views": "Wikimedia Commons", "published": "", "src": "commons",
                    "thumb": ii.get("thumburl", ""), "page": ii.get("descriptionurl")})
    return out


def _archive_item(doc):
    ident = doc["identifier"]
    try:
        files = json.loads(fetch(f"https://archive.org/metadata/{ident}/files")).get("result", [])
    except Exception:
        traceback.print_exc()
        return None
    mp4 = [f for f in files if f.get("name", "").lower().endswith(".mp4")]
    if not mp4:
        return None
    f = min(mp4, key=lambda f: int(f.get("size") or 1 << 62))  # smallest = fastest to stream
    creator = doc.get("creator") or ""
    return {"id": ident, "file": f"https://archive.org/download/{ident}/{urllib.parse.quote(f['name'])}",
            "title": doc.get("title") or ident,
            "channel": creator if isinstance(creator, str) else ", ".join(creator),
            "length": _dur(f.get("length")), "views": "Internet Archive", "published": str(doc.get("year") or ""),
            "src": "archive", "thumb": f"https://archive.org/services/img/{ident}",
            "page": f"https://archive.org/details/{ident}"}


def search_archive(q):
    """Internet Archive: public-domain / CC movies, playable directly in <video>."""
    d = json.loads(fetch(
        "https://archive.org/advancedsearch.php?output=json&rows=10&sort[]=downloads+desc"
        "&fl[]=identifier&fl[]=title&fl[]=creator&fl[]=year&q="
        + urllib.parse.quote(f"({q}) AND mediatype:movies")))
    docs = (d.get("response") or {}).get("docs", [])
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(8) as ex:
        return [r for r in ex.map(_archive_item, docs) if r]


def search_all_videos(q):
    # Hard 8s budget: a slow source is dropped instead of stalling the whole response.
    from concurrent.futures import ThreadPoolExecutor, wait
    ex = ThreadPoolExecutor(3)
    futs = [ex.submit(fn, q) for fn in (search_videos, search_commons, search_archive)]
    wait(futs, timeout=8)
    ex.shutdown(wait=False)
    parts = []
    for f in futs:
        if not f.done():
            print("video source timed out; skipped")
            continue
        try:
            parts.append(f.result())
        except Exception:
            traceback.print_exc()
    return [v for part in parts for v in part]


def search_images(q):
    html = fetch("https://www.bing.com/images/search?form=HDRSC2&first=1&q="
                 + urllib.parse.quote(q))
    out = []
    for raw in re.findall(r'\bm="(\{[^"]+\})"', html):
        try:
            d = json.loads(unescape(raw))
        except ValueError:
            continue
        if d.get("murl"):
            out.append({"title": d.get("t", ""), "image": d["murl"],
                        "thumb": d.get("turl") or d["murl"], "url": d.get("purl")})
    if not out:  # layout fallback: pull image URLs straight out of the page
        txt = unescape(html)
        for murl in dict.fromkeys(re.findall(r'"murl":"(https?://[^"]+)"', txt)):
            out.append({"title": "", "image": murl, "thumb": murl, "url": murl})
    if not out:
        for src in dict.fromkeys(re.findall(r'(https://tse\d\.mm\.bing\.net/th/id/[^"&\s]+)', html)):
            out.append({"title": "", "image": src, "thumb": src, "url": src})
    return out


def wiki_summary(q):
    try:
        s = json.loads(fetch("https://ja.wikipedia.org/w/api.php?action=opensearch&limit=1&format=json&search="
                             + urllib.parse.quote(q)))
        if not s[1]:
            return None
        t = s[1][0]
        d = json.loads(fetch("https://ja.wikipedia.org/api/rest_v1/page/summary/"
                             + urllib.parse.quote(t)))
        return {"title": d.get("title"), "extract": d.get("extract"),
                "thumb": (d.get("thumbnail") or {}).get("source"),
                "url": (d.get("content_urls") or {}).get("desktop", {}).get("page")}
    except Exception:
        traceback.print_exc()
        return None


class H(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _authorized(self):
        # Private mode: set SEEK_PASSWORD (and optionally SEEK_USER) on the host. Unset = open (local dev).
        pw = os.environ.get("SEEK_PASSWORD")
        if not pw:
            return True
        want = base64.b64encode(f"{os.environ.get('SEEK_USER', 'seek')}:{pw}".encode()).decode()
        got = self.headers.get("Authorization", "")
        return hmac.compare_digest(got.encode(), f"Basic {want}".encode())

    def do_GET(self):
        if not self._authorized():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Seek", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        p = urllib.parse.urlparse(self.path)
        if p.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if p.path == "/api/debug":
            info = {}
            for name, url in [("ddg", "https://html.duckduckgo.com/html/?q=test"),
                              ("bing", "https://www.bing.com/search?q=test"),
                              ("yt", "https://www.youtube.com/results?search_query=test"),
                              ("bingimg", "https://www.bing.com/images/search?q=test")]:
                try:
                    h = fetch(url)
                    info[name] = {"len": len(h), "head": h[:300],
                                  "ytdata": "ytInitialData" in h,
                                  "murl": h.count("murl"), "m_attr": h.count(' m="'),
                                  "th": len(re.findall(r"mm\.bing\.net/th", h))}
                except Exception as e:
                    info[name] = {"error": repr(e)}
            return self._send(200, json.dumps(info, ensure_ascii=False), "application/json; charset=utf-8")
        if p.path == "/api/search":
            qs = urllib.parse.parse_qs(p.query)
            q = qs.get("q", [""])[0].strip()
            kind = qs.get("type", ["web"])[0]
            page = int(qs.get("page", ["0"])[0] or 0)
            try:
                if kind == "videos":
                    res = {"results": search_all_videos(q)}
                elif kind == "images":
                    res = {"results": search_images(q)}
                else:
                    from concurrent.futures import ThreadPoolExecutor
                    with ThreadPoolExecutor(2) as ex:  # web + wiki in parallel, not back-to-back
                        wf = ex.submit(wiki_summary, q) if page == 0 else None
                        res = {"results": search_web(q, page), "wiki": wf.result() if wf else None}
            except Exception as e:
                traceback.print_exc()
                res = {"results": [], "error": str(e)}
            return self._send(200, json.dumps(res, ensure_ascii=False), "application/json; charset=utf-8")
        self._send(404, "not found", "text/plain")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", PORT))
    host = "0.0.0.0" if "PORT" in os.environ else "127.0.0.1"
    print(f"http://{host}:{port}")
    ThreadingHTTPServer((host, port), H).serve_forever()
