"""Verify the factual claims the homepage makes, against the live web.

    python tools/check-claims.py [--root DIR] [--offline] [--selftest]

Exit codes are three-state on purpose:
  0  every claim holds
  1  at least one claim is false        <- fix the page
  2  inconclusive (network/dependency) <- re-run somewhere else; NOT a pass, NOT a failure

Conflating 1 and 2 is how a checker gets ignored: a proxy hiccup that reports a
"dead link" trains people to distrust the red output.

Design rules this file lives by:
- Requests carry NO credentials. An authenticated `gh api` returns 200 for a private
  repo, so it structurally cannot catch the failure mode this guards: a link that
  404s for anonymous visitors.
- A claim that cannot be checked is a FINDING, not a silent skip. (Review 2026-09-27:
  deriving the repo from the card href skipped 4 of 9 badges, and a 187-star badge
  drifted to 188 unnoticed, live.)
- `--selftest` mutates a copy and asserts the checker reports it. A check that cannot
  fail is not a check.
"""
import argparse
import datetime
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (claims-check; link and claim verification)"}
CURL = shutil.which("curl")
YUE_README = "https://raw.githubusercontent.com/RevolutionLA/awesome-YuE/HEAD/README.md"


def _get(url, timeout=25):
    req = urllib.request.Request(url, headers=dict(UA))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, b""
    except Exception as e:
        return "err:%s" % (e.reason if isinstance(e, urllib.error.URLError)
                           else type(e).__name__), b""


def _curl_get(url, timeout=25):
    """curl follows redirects and survives the resets this machine's proxy throws
    at python-urllib."""
    if not CURL:
        return None, b""
    try:
        p = subprocess.run(
            [CURL, "-sS", "-L", "-w", "\n%{http_code}", "--max-time", str(timeout), url],
            capture_output=True, timeout=timeout + 5)
    except Exception as e:
        return "err:curl-%s" % type(e).__name__, b""
    out = p.stdout
    i = out.rfind(b"\n")
    if i < 0:
        return "err:curl-no-status", b""
    code, body = out[i + 1:].strip(), out[:i]
    return (int(code) if code.isdigit() else "err:curl-%s" % code.decode("ascii", "replace")), body


def fetch(url, retries=2):
    """Retry transport errors only: a 404 is a real finding, a reset is not."""
    code, body = None, b""
    for i in range(retries):
        code, body = _get(url)
        if not _is_transport(code):
            return code, body
        time.sleep(1.2 * (i + 1))
    ccode, cbody = _curl_get(url)
    return (code, body) if ccode is None else (ccode, cbody)


def _is_transport(code):
    return isinstance(code, str) and code.startswith("err:")


def last_modified(url):
    """Pages' own Last-Modified header - the only date a deployed page can vouch for.
    Returns (code, date_or_None)."""
    req = urllib.request.Request(url, headers=dict(UA), method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            code, raw = r.status, r.headers.get("Last-Modified")
    except urllib.error.HTTPError as e:
        return e.code, None
    except Exception as e:
        return "err:%s" % (e.reason if isinstance(e, urllib.error.URLError)
                           else type(e).__name__), None
    if not raw:
        return code, None
    try:
        # Pages stamps UTC; the sitemap date is a local calendar date. Comparing the
        # two raw makes every push before 08:00 local look a day ahead of itself.
        dt = parsedate_to_datetime(raw)
        return code, dt.astimezone().date() if dt.tzinfo else dt.date()
    except (TypeError, ValueError):
        return code, None


def png_size(path):
    """Read IHDR directly: a missing Pillow is an environment gap, not a false claim."""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n" or len(head) < 24:
        return None
    return struct.unpack(">II", head[16:24])


def _hex_rgb(h):
    h = h.strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lum(rgb):
    def ch(c):
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg, bg):
    a, b = _lum(_hex_rgb(fg)), _lum(_hex_rgb(bg))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def check(root, offline=False):
    """Returns (findings, inconclusive)."""
    findings, unknown = [], []

    def bad(msg):
        findings.append(msg)

    def unk(msg):
        unknown.append(msg)

    idx = open(os.path.join(root, "index.html"), encoding="utf-8").read()
    css = open(os.path.join(root, "assets", "css", "styles.css"), encoding="utf-8").read()
    star_claims = []

    # ---------- static: JSON-LD must parse ----------
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', idx, re.S)
    parsed = []
    for i, b in enumerate(blocks):
        try:
            parsed.append(json.loads(b))
        except ValueError as e:
            parsed.append(None)
            bad("JSON-LD block %d does not parse: %s" % (i + 1, e))

    # ---------- static: Person @id anchor must resolve ----------
    for p in parsed:
        if not p or p.get("@type") != "Person":
            continue
        frag = p.get("@id", "").split("#")[-1]
        if frag and 'id="%s"' % frag not in idx:
            bad("Person @id points at #%s but no element carries that id" % frag)

    # ---------- static: counts agree everywhere ----------
    cards = re.findall(r'<a class="project-card" href="([^"]+)"(.*?)</a>', idx, re.S)
    m = re.search(r"以下 (\d+) 个项目", idx)
    if m and int(m.group(1)) != len(cards):
        bad("prose says %d projects, page renders %d cards" % (int(m.group(1)), len(cards)))
    il = next((p for p in parsed if p and p.get("@type") == "ItemList"), None)
    items = il.get("itemListElement", []) if il else []
    if not il:
        bad("no ItemList block found")
    elif len(items) != len(cards):
        bad("ItemList has %d entries, page has %d cards" % (len(items), len(cards)))
    elif il.get("numberOfItems") != len(items):
        bad("ItemList numberOfItems says %r, actually %d entries"
            % (il.get("numberOfItems"), len(items)))

    # ---------- static: name -> repo map, taken from sameAs (authoritative) ----------
    repo_by_name = {}
    for it in [i.get("item", {}) for i in items]:
        mm = re.search(r"github\.com/([^/]+)/([^/\"?#]+)", str(it.get("sameAs", "")))
        if mm and it.get("name"):
            repo_by_name[it["name"]] = mm.groups()

    card_names = [m.group(1) for _, b in cards for m in [re.search(r'<h4 class="pc-name">(.*?)</h4>', b)] if m]
    for it in [i.get("item", {}) for i in items]:
        if it.get("name") and it["name"] not in card_names:
            bad("ItemList entry '%s' has no card on the page" % it["name"])

    # ---------- static: card <-> ItemList, and case-exact href mapping ----------
    urls = {i["item"].get("url", "") for i in items}
    for href, body in cards:
        nm = re.search(r'<h4 class="pc-name">(.*?)</h4>', body)
        name = nm.group(1) if nm else "?"
        if not any(u.rstrip("/") == href.rstrip("/") for u in urls):
            bad("card '%s' href not in ItemList: %s" % (name, href))
        # GitHub Pages paths are case-sensitive: a "lowercase all URLs" refactor
        # would silently kill the project subpages.
        pages = re.match(r"https://revolutionla\.github\.io/([^/]+)/?$", href)
        if pages:
            seg = pages.group(1)
            repo = repo_by_name.get(name)
            if repo and repo[1] != seg:
                bad("card '%s' href segment '%s' != repo name '%s' (Pages is case-sensitive)"
                    % (name, seg, repo[1]))

    # ---------- static: small text contrast ----------
    vars_ = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,6})", css))
    surfaces = [v for k, v in vars_.items() if k in ("--space-0", "--space-2")]
    if not surfaces:
        bad("could not resolve page/card surfaces to check contrast")
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        fs = re.search(r"font-size:\s*(\d+(?:\.\d+)?)px", body)
        col = re.search(r"(?<![-\w])color:\s*var\((--[\w-]+)\)", body)
        if not (fs and col) or float(fs.group(1)) > 14:
            continue
        fg = vars_.get(col.group(1))
        if not fg:
            continue
        for bg in surfaces:
            c = contrast(fg, bg)
            if c < 4.5:
                bad("%s %s text is %.2f:1 on %s (<4.5:1)"
                    % (sel.strip()[:40], col.group(1), c, bg))
                break

    # ---------- static: heading levels must not skip ----------
    seq = [int(h) for h in re.findall(r"<h([1-6])[\s>]", idx)]
    for a, b in zip(seq, seq[1:]):
        if b > a + 1:
            bad("heading jumps h%d -> h%d" % (a, b))
            break

    # ---------- static: og:image dimensions vs declared ----------
    mm = re.search(r'og:image:width" content="(\d+)"', idx)
    nn = re.search(r'og:image:height" content="(\d+)"', idx)
    if not (mm and nn):
        bad("og:image:width/height meta missing")
    else:
        size = png_size(os.path.join(root, "assets", "img", "og-image.png"))
        if size is None:
            bad("assets/img/og-image.png is not a readable PNG")
        elif size != (int(mm.group(1)), int(nn.group(1))):
            bad("og-image is %dx%d but meta says %sx%s"
                % (size[0], size[1], mm.group(1), nn.group(1)))

    # ---------- static: no stale references ----------
    for name in ("dsh-mate",):
        if name in idx:
            bad("index.html still references %s" % name)

    # ---------- static: every star badge must be CHECKABLE ----------
    # The blind spot that let a 187-star badge drift live: resolving the repo from the
    # card href missed all four `github.io` cards and silently checked nothing.
    # An unverifiable claim is a finding; never `continue` past one.
    for href, body in cards:
        sm = re.search(r"(\d+)★", body)
        if not sm:
            continue
        nm = re.search(r'<h4 class="pc-name">(.*?)</h4>', body)
        name = nm.group(1) if nm else href
        if name not in repo_by_name:
            bad("card '%s' makes a %s claim but has no repo in ItemList sameAs "
                "- checker cannot cover it" % (name, sm.group(0)))
        else:
            star_claims.append((name, repo_by_name[name], int(sm.group(1))))

    # "收录 N 个" / "N 余个" - extract statically so the extraction itself can be
    # audited offline; the comparison against the README needs the network below.
    claims = [(int(m.group(1)), m.group(0))
              for m in re.finditer(r"收录\s*(?:近\s*)?(\d+)\s*(?:余)?\s*个", idx)]
    if not claims:
        bad("no '收录 N 个' claim found - the claim check has gone blind again")

    # ---------- static: sitemap entries ----------
    sm_path = os.path.join(root, "sitemap.xml")
    sm_locs, sm_dates = [], []
    if os.path.exists(sm_path):
        sm = open(sm_path, encoding="utf-8").read()
        sm_locs = re.findall(r"<loc>(.*?)</loc>", sm)
        sm_dates = [(m.group(1), m.group(2)) for m in re.finditer(
            r"<loc>(.*?)</loc>\s*<lastmod>(\d{4}-\d{2}-\d{2})</lastmod>", sm)]
        if not sm_locs:
            bad("sitemap.xml has no <loc> entries")
        if len(sm_dates) != len(sm_locs):
            unk("%d <loc> but %d parseable <lastmod> in sitemap"
                % (len(sm_locs), len(sm_dates)))
        for loc, declared_s in sm_dates:
            if datetime.date.fromisoformat(declared_s) > datetime.date.today():
                bad("sitemap lastmod %s for %s is in the future" % (declared_s, loc))
        for loc in sm_locs:
            seg = re.match(r"https://revolutionla\.github\.io/([^/]+)/?$", loc)
            if not seg:
                continue  # the site root
            if seg.group(1) not in {r[1] for r in repo_by_name.values()}:
                bad("sitemap lists /%s/ but no listed repo is named exactly that "
                    "(Pages paths are case-sensitive)" % seg.group(1))
    else:
        bad("sitemap.xml missing")

    if offline:
        return findings, unknown + ["--offline: link, star and README claims unchecked"]

    # ---------- network: every public URL must answer 200 to an anonymous visitor ----
    anon = set(h for h, _ in cards) | set(sm_locs)
    for i in items:
        for k in ("url", "sameAs"):
            v = i["item"].get(k)
            if isinstance(v, str) and v.startswith("http"):
                anon.add(v)
    for prop, attr in (("og:image", "property"), ("twitter:image", "name")):
        mm = re.search(r'%s="%s" content="(https://[^"]+)"' % (attr, prop), idx)
        if mm:
            anon.add(mm.group(1))
    mm = re.search(r'rel="canonical" href="([^"]+)"', idx)
    if mm:
        anon.add(mm.group(1))
    seen = {}
    for u in sorted(anon):
        seen.setdefault(u.rstrip("/"), u)
    for key, u in sorted(seen.items()):
        code = fetch(u)[0]
        time.sleep(0.3)  # github resets bursts of connections
        if _is_transport(code):
            unk("transport %s for %s (not a verdict)" % (code, u))
        elif code != 200:
            bad("anonymous %s for %s (visitors cannot reach it)" % (code, u))

    # ---------- network: sitemap lastmod must be backed by the deployed page ----------
    # Pages stamps Last-Modified at build time, so it is the only date a live URL can
    # vouch for. Writing this sitemap without that check put 3 of 4 dates a month ahead.
    for loc, declared_s in sm_dates:
        declared = datetime.date.fromisoformat(declared_s)
        code, live = last_modified(loc)
        time.sleep(0.2)
        if _is_transport(code):
            unk("cannot read Last-Modified for %s (%s)" % (loc, code))
        elif live is None:
            unk("no Last-Modified header for %s; lastmod %s unverified" % (loc, declared_s))
        elif declared > live and (declared - live).days > 1:
            bad("sitemap says %s lastmod %s, but the deployed page reports %s"
                % (loc, declared_s, live))
        elif declared > live:
            unk("%s lastmod %s is ahead of the deployed page (%s) - ok only if a push follows"
                % (loc, declared_s, live))

    # ---------- network: star badges against the live repo page ----------
    for name, (owner, r), claimed in star_claims:
        code, n = stars_of(owner, r)
        time.sleep(0.3)
        if n is None:
            unk("cannot read %s/%s anonymously (%s), badge for '%s' unchecked"
                % (owner, r, code, name))
        elif n != claimed:
            bad("%s/%s badge says %d★, GitHub says %d★" % (owner, r, claimed, n))

    # ---------- network: "收录近 70 个" vs the README's real link count ----------
    if claims:
        code, readme = fetch(YUE_README)
        if _is_transport(code):
            unk("cannot fetch awesome-YuE README (%s): '收录' claim unchecked" % code)
        elif code != 200:
            bad("anonymous %s for %s" % (code, YUE_README))
        else:
            links = {"%s/%s" % g for g in re.findall(
                r"github\.com/([^/\"'\s?#]+)/([^/\"'\s?#)]+)",
                readme.decode("utf-8", "replace"))}
            links.discard("RevolutionLA/awesome-YuE")
            n = len(links)
            for base_n, text in claims:
                # "N 余个" = strictly above N; "近 N 个" = just below N
                ok = (n > base_n and n < base_n + 10) if "余" in text else (n <= base_n and n > base_n * 0.9)
                if not ok:
                    bad("claim '%s' vs %d unique repo links in awesome-YuE README" % (text, n))

    return findings, unknown


def stars_of(owner, repo):
    """Scrape the public repo page instead of api.github.com: the API is capped at
    60 anonymous calls/hour, which turns a passing run into a flaky failure."""
    code, body = fetch("https://github.com/%s/%s" % (owner, repo))
    if _is_transport(code):
        return code, None
    if code != 200:
        return code, None
    m = re.search(r'repo-stars-counter-star[^>]*title="([\d,]+)"',
                  body.decode("utf-8", "replace"))
    return (200, int(m.group(1).replace(",", ""))) if m else (code, None)


CASES = [
    # label, target file, old, new, expected substring, dependency
    # dependency: None = pure static; "github" = github.com HTML pages;
    # "raw" = raw.githubusercontent.com; "pages" = this site as deployed on Pages
    ("project count claim", "index.html", "以下 9 个项目", "以下 42 个项目", "prose says", None),
    ("dead link", "index.html", "https://github.com/RevolutionLA/shijing",
     "https://github.com/RevolutionLA/thisRepoDoesNotExist-42", "visitors cannot reach", "github"),
    ("sub-threshold small text", "assets/css/styles.css",
     "--ink-faint: #7a8798", "--ink-faint: #5d6878", "text is", None),
    ("stale star badge", "index.html", "开源工作站 · 3★", "开源工作站 · 999★", "badge says", "github"),
    ("star badge the checker cannot cover", "index.html",
     '<h4 class="pc-name">adversarial-review</h4>', '<h4 class="pc-name">adversarial-reviewx</h4>',
     "no repo in ItemList sameAs", None),
    ("false 收录 claim", "index.html", "收录近 70 个", "收录近 900 个", "claim '", "raw"),
    ("收录 claim removed", "index.html", "收录近 70 个", "收录一批", "gone blind", None),
    ("heading skip", "index.html",
     '<h3 class="pg-head mono"><span class="pg-label">AI · 音乐创作</span>'
     '<span class="pg-rule" aria-hidden="true"></span></h3>',
     '<h5 class="pg-head mono"><span class="pg-label">AI · 音乐创作</span>'
     '<span class="pg-rule" aria-hidden="true"></span></h5>', "heading jumps", None),
    ("og size mismatch", "index.html", 'og:image:width" content="1200"',
     'og:image:width" content="1201"', "og-image is", None),
    ("sitemap repo name wrong case", "sitemap.xml", "/AscendMate/", "/ascendmate/",
     "case-sensitive", None),
    ("sitemap lastmod ahead of the deployed page", "sitemap.xml",
     "<lastmod>2026-08-23</lastmod>", "<lastmod>2026-09-26</lastmod>",
     "deployed page reports", "pages"),
]


def probe(dep):
    """Which external host this case actually needs. Asserting on the real dependency,
    not on 'is the internet up', is what keeps a skip honest instead of a false pass."""
    if dep == "github":
        return stars_of("RevolutionLA", "adversarial-review")[1] is not None
    if dep == "raw":
        return fetch(YUE_README)[0] == 200
    if dep == "pages":
        return last_modified("https://revolutionla.github.io/")[1] is not None
    return True


def selftest(root):
    """Mutate a copy; the checker must notice each kind of breakage."""
    import tempfile
    rc = 0
    skipped = []
    for label, target, old, new, expect, dep in CASES:
        if dep and not probe(dep):
            skipped.append("%s (%s host unreachable)" % (label, dep))
            print("selftest [%s] SKIPPED - depends on unreachable host" % label)
            continue
        tmp = tempfile.mkdtemp(prefix="claims-selftest-")
        try:
            for rel in ("index.html", "assets/css/styles.css", "assets/img/og-image.png",
                        "sitemap.xml"):
                src = os.path.join(root, rel)
                dst = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(src, dst)
            p = os.path.join(tmp, target)
            s = open(p, encoding="utf-8").read()
            if old not in s:
                print("selftest [%s] BROKEN - anchor text absent, case is stale" % label)
                rc = 1
                continue
            open(p, "w", encoding="utf-8").write(s.replace(old, new))
            found, _ = check(tmp, offline=dep is None)
            if any(expect in f for f in found):
                print("selftest OK - caught %s" % label)
            else:
                print("selftest FAILED - blind to %s: %r" % (label, found))
                rc = 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if skipped:
        print("(%d case(s) skipped: %s)" % (len(skipped), "; ".join(skipped)))
    print("%d case(s) ran, %d skipped" % (len(CASES) - len(skipped), len(skipped)))
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--offline", action="store_true", help="skip every network check")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest(a.root))
    found, unknown = check(a.root, offline=a.offline)
    for f in found:
        print("FAIL   %s" % f)
    for u in unknown:
        print("UNSURE %s" % u)
    print("%d finding(s), %d inconclusive" % (len(found), len(unknown)))
    if found:
        sys.exit(1)
    sys.exit(2 if unknown else 0)


if __name__ == "__main__":
    main()
