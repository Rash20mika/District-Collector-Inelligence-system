"""Local hybrid index (keyword BM25 + semantic embeddings) over APPROVED sources only.

Design rules
- Nothing here touches the network by itself. The caller passes a `fetch(source_id, url)`
  function (app.py passes one built on request_approved), so the allowlist is still enforced
  in one place. fetch must return a requests.Response (a plain str is also accepted for HTML).
- Every chunk keeps source_id, source_name, evidence_level, url, published_at and fetched_at, so
  answers can be cited and the Collector can see how old the information is.
- If the embedding model cannot be loaded, search quietly falls back to keyword-only.

Whole-site crawling (this version)
- Every enabled source in sources.yaml is crawled unless it has `index: false`.
- Pages are discovered from: the source url + topic_paths + feed_url, sitemap.xml (and any
  Sitemap: lines in robots.txt), and every same-domain link found on each page (any depth).
- Per-source page cap: `max_pages` in sources.yaml (default INDEX_MAX_PAGES, 300).
- HTML pages and text-based PDFs are indexed (scanned PDFs have no text and are skipped).
- robots.txt is honoured (INDEX_RESPECT_ROBOTS=0 turns this off; only do that with permission).
- Re-builds are incremental: unchanged chunks reuse their old embeddings, pages whose sitemap
  <lastmod> is unchanged are not re-downloaded, and a page that fails to download keeps its
  previous copy instead of disappearing.
"""
import hashlib
import html
import io
import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import parse_qs, urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

INDEX_DIR = Path(os.getenv("INDEX_DIR", str(Path(__file__).resolve().parent / "index_data")))
CHUNKS_FILE = INDEX_DIR / "chunks.json"
EMB_FILE = INDEX_DIR / "embeddings.npy"
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
MIN_COSINE = float(os.getenv("INDEX_MIN_COSINE", "0.35"))

DEFAULT_MAX_PAGES = int(os.getenv("INDEX_MAX_PAGES", "300"))
CRAWL_DELAY = float(os.getenv("INDEX_DELAY_SECONDS", "0.3"))
RESPECT_ROBOTS = os.getenv("INDEX_RESPECT_ROBOTS", "1") != "0"
MAX_PDF_PAGES = 80
ROBOTS_AGENT = "DistrictIntelligence"

SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".zip", ".rar", ".doc", ".docx", ".xls",
            ".xlsx", ".ppt", ".pptx", ".mp4", ".mp3", ".avi", ".css", ".js", ".ico", ".svg",
            ".woff", ".woff2", ".ttf", ".apk", ".exe")
# query parameters that create endless near-duplicate pages (site search, sorting, printing...)
DENY_QUERY_KEYS = {"s", "q", "search", "query", "replytocom", "sort", "order", "print", "share"}
SKIP_PATH_RE = re.compile(r"/(wp-admin|wp-login\.php|wp-json|xmlrpc\.php|cdn-cgi|feed)(/|$)", re.I)

STOP = {"the", "a", "an", "of", "in", "on", "at", "for", "to", "and", "or", "is", "are",
        "was", "were", "what", "which", "who", "me", "my", "give", "show", "tell", "please",
        "about", "with", "from", "by", "this", "that", "it", "be", "list"}
# \w alone does not cover Tamil vowel signs / virama, which would split Tamil words into pieces
TOKEN_RE = re.compile(r"[\w\u0B80-\u0BFF]+")


# ------------------------------------------------------------
# Text helpers
# ------------------------------------------------------------
def page_text(raw: str) -> str:
    raw = re.sub(r"(?s)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?is)<(script|style|noscript|nav|header|footer|form)\b.*?</\1>", " ", raw)
    main = re.search(r"(?is)<main\b[^>]*>(.*?)</main>", raw)
    if main:
        raw = main.group(1)
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", raw))
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)  # "ofRs" -> "of Rs", "ThozhilMunaivor" -> "Thozhil Munaivor"
    return re.sub(r"\s+", " ", text).strip()


def page_title(raw: str, fallback: str) -> str:
    for pattern in (r"(?is)<h1\b[^>]*>(.*?)</h1>", r"(?is)<title\b[^>]*>(.*?)</title>"):
        m = re.search(pattern, raw)
        if m:
            t = re.sub(r"\s+", " ", html.unescape(re.sub(r"(?s)<[^>]+>", " ", m.group(1)))).strip()
            if t:
                return t[:150]
    return fallback


DATE_PATTERNS = [
    r'(?is)<meta[^>]+(?:article:published_time|datePublished|og:updated_time|DC\.date)[^>]+content=["\']([^"\']+)',
    r'(?is)"datePublished"\s*:\s*"([^"]+)"',
    r'(?is)<time\b[^>]*datetime=["\']([^"\']+)',
    r'(?is)(?:published|posted|last\s+updated|updated|date)\s*(?:on|date)?\s*[:\-]?\s*'
    r'(\d{1,2}[\s/\-.](?:\d{1,2}|[A-Za-z]{3,9})[\s/\-.,]*\d{2,4})',
]


def extract_published_date(raw: str) -> str:
    """Best-effort page date from meta tags, JSON-LD, <time>, or visible text. '' if none."""
    for pat in DATE_PATTERNS:
        m = re.search(pat, raw)
        if m:
            return m.group(1).strip()[:40]
    return ""


def chunk_text(text: str, target: int = 700) -> List[str]:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    pieces: List[str] = []
    for s in sentences:  # very long "sentences" (lists with no full stops) are split, never cut off
        while len(s) > target * 1.5:
            cut = s.rfind(" ", 0, target)
            cut = cut if cut > target // 2 else target
            pieces.append(s[:cut].strip())
            s = s[cut:].strip()
        if s:
            pieces.append(s)
    chunks, current, last = [], "", ""
    for piece in pieces:
        if current and len(current) + len(piece) > target:
            chunks.append(current)
            current = f"{last} {piece}" if last and len(last) < target // 2 else piece  # overlap
        else:
            current = f"{current} {piece}".strip()
        last = piece
    if current:
        chunks.append(current)
    return [c for c in chunks if len(c) >= 30]


def tokenize(text: str) -> List[str]:
    out = []
    for tok in TOKEN_RE.findall(text.lower()):
        if len(tok) < 2 or tok in STOP:
            continue
        if len(tok) > 3 and tok.endswith("s"):
            tok = tok[:-1]
        out.append(tok)
    return out


def chunk_key(c: Dict[str, Any]) -> str:
    return hashlib.sha1(f"{c.get('title', '')}. {c['text']}".encode("utf-8")).hexdigest()


# ------------------------------------------------------------
# URL / link helpers
# ------------------------------------------------------------
def canonical(url: str) -> Optional[str]:
    """Normalised URL (no fragment). None for URLs that should never be crawled."""
    try:
        p = urlparse(url.strip())
    except ValueError:
        return None
    if p.scheme != "https" or not p.hostname:
        return None
    path = p.path or "/"
    if path.lower().endswith(SKIP_EXT) or SKIP_PATH_RE.search(path):
        return None
    if p.query and ({k.lower() for k in parse_qs(p.query)} & DENY_QUERY_KEYS):
        return None
    return urlunparse((p.scheme, p.netloc.lower(), path, "", p.query, ""))


def url_key(url: str) -> str:
    return url.rstrip("/")


def extract_links(raw: str, base_url: str, allowed_domains: List[str]) -> List[str]:
    links, seen = [], set()
    for href in re.findall(r"(?is)<a\b[^>]*href=[\"']([^\"'#]+)", raw):
        url = canonical(urljoin(base_url, html.unescape(href).strip()))
        if not url or urlparse(url).hostname not in allowed_domains or url in seen:
            continue
        seen.add(url)
        links.append(url)
    return links


def parse_xml_links(data: Any):
    """Sitemap / sitemap-index / RSS / Atom -> ([(url, lastmod)], [child sitemap urls])."""
    try:
        root = ET.fromstring(data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8"))
    except ET.ParseError:
        return [], []

    def local(tag: str) -> str:
        return tag.split("}")[-1]

    pages, subs = [], []
    kind = local(root.tag)
    if kind == "sitemapindex":
        for sm in root:
            for ch in sm:
                if local(ch.tag) == "loc" and ch.text:
                    subs.append(ch.text.strip())
    elif kind == "urlset":
        for u in root:
            loc = lastmod = None
            for ch in u:
                if local(ch.tag) == "loc" and ch.text:
                    loc = ch.text.strip()
                elif local(ch.tag) == "lastmod" and ch.text:
                    lastmod = ch.text.strip()
            if loc:
                pages.append((loc, lastmod))
    else:  # RSS / Atom feed
        for el in root.iter():
            tag = local(el.tag)
            if tag == "item":
                for ch in el:
                    if local(ch.tag) == "link" and ch.text:
                        pages.append((ch.text.strip(), None))
            elif tag == "entry":
                for ch in el:
                    if local(ch.tag) == "link" and ch.get("href"):
                        pages.append((ch.get("href"), None))
    return pages, subs


def _is_response(resp: Any) -> bool:
    return not isinstance(resp, (str, bytes, bytearray))


def _body_text(resp: Any) -> str:
    return resp.text if _is_response(resp) else (resp if isinstance(resp, str) else resp.decode("utf-8", "replace"))


def _body_bytes(resp: Any) -> bytes:
    if _is_response(resp):
        return resp.content
    return resp.encode("utf-8") if isinstance(resp, str) else bytes(resp)


def doc_kind(url: str, resp: Any) -> str:
    ctype = str(resp.headers.get("Content-Type", "")).lower() if _is_response(resp) else ""
    path = urlparse(url).path.lower()
    if "html" in ctype:
        return "html"
    if "pdf" in ctype or path.endswith(".pdf"):
        return "pdf"
    if "xml" in ctype or path.endswith(".xml"):
        return "xml"
    return "html"


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader  # optional dependency: pip install pypdf
    reader = PdfReader(io.BytesIO(data))
    parts = [(page.extract_text() or "") for page in reader.pages[:MAX_PDF_PAGES]]
    return re.sub(r"\s+", " ", " ".join(parts)).strip()[:300000]


class Robots:
    """robots.txt per host, fetched through the same approved `fetch` (so the allowlist applies)."""

    def __init__(self, fetch: Callable, source_id: str, enabled: bool = True):
        self.fetch, self.source_id, self.enabled = fetch, source_id, enabled
        self.parsers: Dict[str, Optional[RobotFileParser]] = {}
        self.sitemaps: Dict[str, List[str]] = {}

    def _load(self, host: str):
        if host in self.parsers:
            return
        self.sitemaps[host] = []
        try:
            resp = self.fetch(self.source_id, f"https://{host}/robots.txt")
            rp = RobotFileParser()
            rp.parse(_body_text(resp).splitlines())
            self.parsers[host] = rp
            self.sitemaps[host] = list(rp.site_maps() or [])
        except Exception:
            self.parsers[host] = None  # no readable robots.txt -> allowed

    def allowed(self, url: str) -> bool:
        if not self.enabled:
            return True
        host = urlparse(url).hostname
        self._load(host)
        rp = self.parsers.get(host)
        return True if rp is None else rp.can_fetch(ROBOTS_AGENT, url)

    def sitemaps_for(self, url: str) -> List[str]:
        if not self.enabled:
            return []
        host = urlparse(url).hostname
        self._load(host)
        return self.sitemaps.get(host, [])


# ------------------------------------------------------------
# Embeddings (optional)
# ------------------------------------------------------------
def load_embedder():
    """Returns (embed_fn, error). embed_fn(list[str]) -> normalised float32 matrix."""
    try:
        import numpy as np
        from fastembed import TextEmbedding
        model = TextEmbedding(EMBED_MODEL)

        def embed(texts: List[str]):
            vectors = []
            for i in range(0, len(texts), 16):  # small batches: avoids out-of-memory errors
                vectors.extend(model.embed(texts[i:i + 16], batch_size=4))
            mat = np.array(vectors, dtype="float32")
            norms = np.linalg.norm(mat, axis=1, keepdims=True)
            return mat / np.maximum(norms, 1e-9)

        return embed, None
    except Exception as e:  # not installed, offline, model download failed...
        return None, str(e)


# ------------------------------------------------------------
# Hybrid index
# ------------------------------------------------------------
class HybridIndex:
    def __init__(self, chunks: List[Dict[str, Any]], embeddings=None, embedder: Optional[Callable] = None):
        self.chunks = chunks
        self.embeddings = embeddings
        self.embedder = embedder
        self.tf = [Counter(tokenize(f"{c.get('title', '')} {c['text']}")) for c in chunks]
        self.dl = [sum(t.values()) for t in self.tf]
        self.postings: Dict[str, List] = defaultdict(list)  # term -> [(chunk index, term freq)]
        for i, counter in enumerate(self.tf):
            for term, freq in counter.items():
                self.postings[term].append((i, freq))
        self.avg_len = (sum(self.dl) / len(self.dl)) if self.dl else 1.0
        self.vocab = list(self.postings)

    @property
    def mode(self) -> str:
        return "hybrid (keyword + semantic)" if (self.embeddings is not None and self.embedder) else "keyword-only"

    def _bm25(self, query_tokens: List[str]) -> List[float]:
        n, k1, b = len(self.chunks), 1.5, 0.75
        scores = [0.0] * n
        for term in set(query_tokens):
            # exact matches at full weight; for longer words also tokens that merely CONTAIN the word
            # (websites often glue words together, e.g. "applicantage"), at reduced weight
            matches = [(term, 1.0)] if term in self.postings else []
            if len(term) >= 5:
                matches += [(tok, 0.4) for tok in self.vocab if term in tok and tok != term]
            for tok, weight in matches:
                plist = self.postings[tok]
                df = len(plist)
                idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
                for i, f in plist:
                    scores[i] += weight * idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self.dl[i] / self.avg_len))
        return scores

    def search(self, query: str, k: int = 5, aliases: Dict[str, List[str]] = None) -> List[Dict[str, Any]]:
        # spelling variants (e.g. Thirumangalam / Tirumangalam): add the other spellings
        lowered = query.lower()
        extra = []
        for canonical_name, alts in (aliases or {}).items():
            names = [canonical_name] + list(alts or [])
            if any(re.search(rf"\b{re.escape(n.lower())}\b", lowered) for n in names):
                extra.extend(names)
        expanded = f"{query} {' '.join(extra)}" if extra else query

        kw_scores = self._bm25(tokenize(expanded))
        kw_rank = [i for i in sorted(range(len(kw_scores)), key=lambda i: -kw_scores[i])
                   if kw_scores[i] > 0][:30]

        sem_rank, cos = [], {}
        if self.embeddings is not None and self.embedder:
            import numpy as np
            qv = self.embedder([expanded])[0]
            sims = self.embeddings @ qv
            for i in np.argsort(-sims)[:30]:
                if sims[i] >= MIN_COSINE:
                    sem_rank.append(int(i))
                    cos[int(i)] = float(sims[i])

        fused: Dict[int, float] = {}
        for rank_list in (kw_rank, sem_rank):
            for pos, i in enumerate(rank_list):
                fused[i] = fused.get(i, 0.0) + 1.0 / (60 + pos + 1)  # reciprocal rank fusion
        top = sorted(fused, key=lambda i: -fused[i])

        hits, per_url = [], Counter()
        for i in top:
            c = self.chunks[i]
            if per_url[c["url"]] >= 2:  # avoid one page filling every slot
                continue
            per_url[c["url"]] += 1
            hits.append({**c, "score": round(fused[i], 4),
                         "matched_by": [n for n, lst in (("keyword", kw_rank), ("semantic", sem_rank)) if i in lst]})
            if len(hits) >= k:
                break
        return hits


# ------------------------------------------------------------
# Crawl
# ------------------------------------------------------------
def indexable_sources(sources: Dict[str, Dict[str, Any]]):
    """Every enabled source with a url, except search APIs and sources marked `index: false`."""
    for sid, src in sources.items():
        if not src.get("enabled") or src.get("index") is False:
            continue
        if src.get("type") == "search_api" or not src.get("url"):
            continue
        yield sid, src


def crawl_source(sid: str, src: Dict[str, Any], fetch: Callable, robots: Robots, claimed: set,
                 old_pages: Dict[str, Any], old_by_url: Dict[str, List[Dict[str, Any]]],
                 log: Callable[[str], None], delay: float) -> Dict[str, Any]:
    domains = src.get("domains", [])
    max_pages = int(src.get("max_pages") or DEFAULT_MAX_PAGES)
    queue: deque = deque()
    queued, lastmods = set(), {}

    def enqueue(url: str, lastmod: Optional[str] = None):
        c = canonical(urljoin(src["url"], url))
        if not c or urlparse(c).hostname not in domains:
            return
        key = url_key(c)
        if lastmod:  # remember the sitemap date even if a page link queued this URL first
            lastmods[key] = lastmod
        if key in queued or key in claimed:
            return
        queued.add(key)
        queue.append(c)

    seeds = [src["url"], src.get("feed_url")]
    seeds += [urljoin(src["url"], p) for p in (src.get("topic_paths") or {}).values()]
    for seed in seeds:
        if seed:
            enqueue(seed)
    for sm in dict.fromkeys([urljoin(src["url"], "/sitemap.xml")] + robots.sitemaps_for(src["url"])):
        enqueue(sm)

    pages, stats = [], {"pages_ok": 0, "pages_failed": 0, "pages_reused": 0,
                        "blocked_by_robots": 0, "pdfs": 0, "capped": False}
    fetched = 0
    pdf_missing_logged = False
    while queue and fetched < max_pages:
        url = queue.popleft()
        key = url_key(url)
        if key in claimed:
            continue
        claimed.add(key)
        if not robots.allowed(url):
            stats["blocked_by_robots"] += 1
            continue

        old = old_pages.get(url)
        lastmod = lastmods.get(key)
        if lastmod and old and old.get("lastmod") == lastmod and old_by_url.get(url):
            pages.append({"url": url, "reused_chunks": old_by_url[url]})
            stats["pages_reused"] += 1
            fetched += 1
            continue

        if fetched:
            time.sleep(delay)
        try:
            resp = fetch(sid, url)
        except Exception as e:
            if not url.endswith(("sitemap.xml", "robots.txt")):
                stats["pages_failed"] += 1
                log(f"  skipped {url}: {str(e)[:80]}")
                if old_by_url.get(url):  # keep the previous copy rather than losing the page
                    pages.append({"url": url, "reused_chunks": old_by_url[url]})
            continue

        kind = doc_kind(url, resp)
        if kind == "xml":  # sitemap / feed: discover pages, not counted as a page
            found, subs = parse_xml_links(_body_bytes(resp))
            for loc, mod in found:
                enqueue(loc, mod)
            for sub in subs:
                enqueue(sub)
            continue

        fetched += 1
        if fetched % 25 == 0:
            log(f"  {src.get('name', sid)}: {fetched} pages so far ({len(queue)} waiting)")
        if kind == "pdf":
            try:
                text = pdf_text(_body_bytes(resp))
            except ImportError:
                if not pdf_missing_logged:
                    log("  PDFs skipped: install pypdf (pip install pypdf)")
                    pdf_missing_logged = True
                continue
            except Exception as e:
                stats["pages_failed"] += 1
                log(f"  skipped PDF {url}: {str(e)[:80]}")
                continue
            title = urlparse(url).path.rsplit("/", 1)[-1] or src.get("name", sid)
            published = ""
            stats["pdfs"] += 1
        else:
            raw = _body_text(resp)
            for link in extract_links(raw, url, domains):
                enqueue(link)
            text = page_text(raw)
            title = page_title(raw, src.get("name", sid))
            published = extract_published_date(raw)
        stats["pages_ok"] += 1
        pages.append({"url": url, "kind": kind, "text": text, "title": title,
                      "published_at": published, "lastmod": lastmod})

    if queue and fetched >= max_pages:
        stats["capped"] = True
        stats["pages_waiting"] = len(queue)
    return {"pages": pages, "stats": stats}


# ------------------------------------------------------------
# Build / load
# ------------------------------------------------------------
def _load_previous():
    """Previous index (for incremental rebuilds): data, chunks by url, chunks by source, vectors by key."""
    if not CHUNKS_FILE.exists():
        return {}, defaultdict(list), defaultdict(list), {}
    try:
        data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}, defaultdict(list), defaultdict(list), {}
    by_url, by_source = defaultdict(list), defaultdict(list)
    for c in data.get("chunks", []):
        by_url[c["url"]].append(c)
        by_source[c["source_id"]].append(c)
    vectors = {}
    if EMB_FILE.exists() and data.get("embed_model") == EMBED_MODEL:
        try:
            import numpy as np
            mat = np.load(EMB_FILE)
            if len(mat) == len(data.get("chunks", [])):
                vectors = {chunk_key(c): mat[i] for i, c in enumerate(data["chunks"])}
        except Exception:
            pass
    return data, by_url, by_source, vectors


def build_index(sources: Dict[str, Dict[str, Any]], fetch: Callable[[str, str], Any],
                log: Callable[[str], None] = print, embedder: Optional[Callable] = None,
                crawl_delay: Optional[float] = None) -> Dict[str, Any]:
    """Crawl every indexable approved source (whole site, up to max_pages) and save the index."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    delay = CRAWL_DELAY if crawl_delay is None else crawl_delay
    prev, old_by_url, old_by_source, old_vectors = _load_previous()
    old_pages = prev.get("pages", {})

    chunks: List[Dict[str, Any]] = []
    seen_text, claimed, report, page_meta = set(), set(), {}, {}

    def add_chunk(sid, src, url, title, text, published, fetched_at, doc_type):
        key = hashlib.sha1(text.encode("utf-8")).hexdigest()  # identical text only (shared boilerplate)
        if key in seen_text:
            return False
        seen_text.add(key)
        chunks.append({"id": len(chunks), "source_id": sid, "source_name": src.get("name", sid),
                       "evidence_level": src.get("evidence_level", ""), "url": url, "title": title,
                       "text": text, "published_at": published, "doc_type": doc_type,
                       "fetched_at": fetched_at})
        return True

    for sid, src in indexable_sources(sources):
        log(f"Crawling {src.get('name', sid)} (up to {int(src.get('max_pages') or DEFAULT_MAX_PAGES)} pages)...")
        robots = Robots(fetch, sid, RESPECT_ROBOTS)
        result = crawl_source(sid, src, fetch, robots, claimed, old_pages, old_by_url, log, delay)
        added = 0
        for page in result["pages"]:
            url = page["url"]
            if page.get("reused_chunks") is not None:
                for c in page["reused_chunks"]:
                    added += add_chunk(sid, src, url, c.get("title", ""), c["text"],
                                       c.get("published_at", ""), c.get("fetched_at", now),
                                       c.get("doc_type", "html"))
                old = old_pages.get(url, {})
                page_meta[url] = {"lastmod": old.get("lastmod"), "fetched_at": old.get("fetched_at", now),
                                  "source_id": sid}
                continue
            for piece in chunk_text(page["text"]):
                added += add_chunk(sid, src, url, page["title"], piece, page.get("published_at", ""),
                                   now, "pdf" if page["kind"] == "pdf" else "html")
            page_meta[url] = {"lastmod": page.get("lastmod"), "fetched_at": now, "source_id": sid}

        if not result["pages"] and old_by_source.get(sid):  # whole source unreachable: keep old copy
            for c in old_by_source[sid]:
                added += add_chunk(sid, src, c["url"], c.get("title", ""), c["text"],
                                   c.get("published_at", ""), c.get("fetched_at", now), c.get("doc_type", "html"))
            log(f"  {src.get('name', sid)} unreachable: kept the previous copy ({added} passages)")
        report[sid] = {**result["stats"], "chunks": added}
        s = result["stats"]
        log(f"{src.get('name', sid)}: {s['pages_ok']} pages ({s['pdfs']} PDFs), {s['pages_reused']} unchanged, "
            f"{s['pages_failed']} failed, {s['blocked_by_robots']} blocked by robots.txt, {added} passages"
            + (f" - CAPPED, {s.get('pages_waiting', 0)} pages not visited (raise max_pages)" if s["capped"] else ""))

    INDEX_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CHUNKS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"built_at": now, "embed_model": EMBED_MODEL, "pages": page_meta,
                               "chunks": chunks}, ensure_ascii=False), encoding="utf-8")

    emb_status = "none"
    if chunks:
        embed, err = (embedder, None) if embedder else load_embedder()
        if embed:
            try:
                import numpy as np
                keys = [chunk_key(c) for c in chunks]
                missing = [i for i, k in enumerate(keys) if k not in old_vectors]
                new_vecs = embed([f"{chunks[i]['title']}. {chunks[i]['text']}" for i in missing]) if missing else []
                by_index = {i: new_vecs[n] for n, i in enumerate(missing)}
                mat = np.array([old_vectors[k] if k in old_vectors else by_index[i]
                                for i, k in enumerate(keys)], dtype="float32")
                emb_tmp = EMB_FILE.with_suffix(".tmp.npy")
                np.save(emb_tmp, mat)
                os.replace(emb_tmp, EMB_FILE)
                emb_status = f"saved ({len(missing)} new, {len(chunks) - len(missing)} reused)"
            except Exception as e:
                if EMB_FILE.exists():
                    EMB_FILE.unlink()
                emb_status = f"failed ({str(e)[:120]})"
                log(f"Embedding failed, keyword-only search will be used: {emb_status}")
        else:
            if EMB_FILE.exists():
                EMB_FILE.unlink()
            emb_status = f"unavailable ({err[:120] if err else 'unknown'})"
            log(f"Embeddings not created, keyword-only search will be used: {emb_status}")
    os.replace(tmp, CHUNKS_FILE)
    reset_cache()
    return {"built_at": now, "total_chunks": len(chunks), "total_pages": len(page_meta),
            "per_source": report, "embeddings": emb_status}


_CACHE: Dict[str, Any] = {"index": None, "mtime": None}


def reset_cache():
    _CACHE.update({"index": None, "mtime": None})


def load_index() -> Optional[HybridIndex]:
    if not CHUNKS_FILE.exists():
        return None
    mtime = CHUNKS_FILE.stat().st_mtime
    if _CACHE["index"] is not None and _CACHE["mtime"] == mtime:
        return _CACHE["index"]
    data = json.loads(CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = data.get("chunks", [])
    embeddings, embedder = None, None
    if EMB_FILE.exists() and chunks and data.get("embed_model") == EMBED_MODEL:
        try:
            import numpy as np
            mat = np.load(EMB_FILE)
            if len(mat) == len(chunks):
                embed, _ = load_embedder()
                if embed:
                    embeddings, embedder = mat, embed
        except Exception:
            pass
    idx = HybridIndex(chunks, embeddings, embedder)
    idx.built_at = data.get("built_at")
    _CACHE.update({"index": idx, "mtime": mtime})
    return idx