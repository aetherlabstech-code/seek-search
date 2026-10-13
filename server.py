"""Mini meta search engine: web / images / videos (playable inline).
Run: python server.py  ->  http://localhost:8765
No API keys, stdlib only.
"""
import json
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
    with urllib.request.urlopen(req, timeout=12) as r:
        return r.read().decode("utf-8", "replace")


def strip_tags(s):
    return unescape(re.sub(r"<[^>]+>", "", s)).strip()


def search_web(q, page=0):
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
                    "thumb": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"})
    return out


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

    def do_GET(self):
        p = urllib.parse.urlparse(self.path)
        if p.path in ("/", "/index.html"):
            return self._send(200, (HERE / "index.html").read_bytes(), "text/html; charset=utf-8")
        if p.path == "/api/search":
            qs = urllib.parse.parse_qs(p.query)
            q = qs.get("q", [""])[0].strip()
            kind = qs.get("type", ["web"])[0]
            page = int(qs.get("page", ["0"])[0] or 0)
            try:
                if kind == "videos":
                    res = {"results": search_videos(q)}
                elif kind == "images":
                    res = {"results": search_images(q)}
                else:
                    res = {"results": search_web(q, page),
                           "wiki": wiki_summary(q) if page == 0 else None}
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
