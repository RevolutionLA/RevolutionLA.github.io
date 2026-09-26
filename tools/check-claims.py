"""Verify the factual claims the homepage makes, against the live web.

    python tools/check-claims.py [--root DIR] [--selftest]

Exit 0 = every claim holds, 1 = at least one is stale/false.

Design notes (why this script exists in this shape):
- Requests are made WITHOUT credentials. An authenticated `gh api` call returns
  200 for a private repo, so it can never catch the failure mode this guards:
  a link that 404s for anonymous visitors.
- `--selftest` mutates a copy of the site and asserts the checker reports it.
  A check that cannot fail is not a check.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UA = {"User-Agent": "Mozilla/5.0 (claims-check; link and claim verification)"}


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


def http_status(url, retries=3):
    """Retry transport errors only: a 404 is a real finding, a reset is not."""
    code = None
    for i in range(retries):
        code = _get(url)[0]
        if not (isinstance(code, str) and code.startswith("err:")):
            return code
        time.sleep(1.5 * (i + 1))
    return code


def stars_of(owner, repo):
    """Scrape the public repo page instead of api.github.com: the API is capped at
    60 anonymous calls/hour, which turns a passing run into a flaky failure."""
    code, body = _get("https://github.com/%s/%s" % (owner, repo))
    if code != 200:
        return code, None
    m = re.search(r'repo-stars-counter-star[^>]*title="([\d,]+)"',
                  body.decode("utf-8", "replace"))
    return (200, int(m.group(1).replace(",", ""))) if m else (code, None)


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


# small text that must clear WCAG AA 4.5:1 is discovered from the CSS, not listed here


def check(root):
    findings = []
    idx = open(os.path.join(root, "index.html"), encoding="utf-8").read()
    css = open(os.path.join(root, "assets", "css", "styles.css"),
               encoding="utf-8").read()

    def bad(msg):
        findings.append(msg)

    # 1. JSON-LD must parse
    blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>',
                        idx, re.S)
    parsed = []
    for i, b in enumerate(blocks):
        try:
            parsed.append(json.loads(b))
        except ValueError as e:
            parsed.append(None)
            bad("JSON-LD block %d does not parse: %s" % (i + 1, e))

    # 2. Person @id anchor must resolve to a real element id
    for p in parsed:
        if not p or p.get("@type") != "Person":
            continue
        frag = p.get("@id", "").split("#")[-1]
        if frag and 'id="%s"' % frag not in idx:
            bad("Person @id points at #%s but no element carries that id" % frag)

    # 3. card count == prose count == ItemList length
    cards = re.findall(r'<a class="project-card" href="([^"]+)"(.*?)</a>',
                       idx, re.S)
    m = re.search(r"以下 (\d+) 个项目", idx)
    if m:
        n = int(m.group(1))
        if n != len(cards):
            bad("prose says %d projects, page renders %d cards" % (n, len(cards)))
    il = next((p for p in parsed if p and p.get("@type") == "ItemList"), None)
    if il:
        items = il.get("itemListElement", [])
        if len(items) != len(cards):
            bad("ItemList has %d entries, page has %d cards" % (len(items), len(cards)))
        names = {i["item"]["name"] for i in items}
        for _, body in cards:
            nm = re.search(r'<h4 class="pc-name">(.*?)</h4>', body)
            if nm and nm.group(1) not in names:
                bad("card '%s' has no matching ItemList entry" % nm.group(1))
        urls = {i["item"]["url"] for i in items}
        for href, _ in cards:
            if not any(u.rstrip("/") == href.rstrip("/") for u in urls):
                bad("card href not in ItemList: %s" % href)

    # 4. every public URL must answer 200 to an anonymous visitor
    anon = set(h for h, _ in cards)
    if il:
        for i in il.get("itemListElement", []):
            it = i["item"]
            for k in ("url", "sameAs"):
                v = it.get(k)
                if isinstance(v, str) and v.startswith("http"):
                    anon.add(v)
    for prop, name in (("og:image", "property"), ("twitter:image", "name")):
        mm = re.search(r'%s="%s" content="(https://[^"]+)"' % (name, prop), idx)
        if mm:
            anon.add(mm.group(1))
    mm = re.search(r'rel="canonical" href="([^"]+)"', idx)
    if mm:
        anon.add(mm.group(1))
    seen = {}
    for u in sorted(anon):
        seen.setdefault(u.rstrip("/"), u)  # card href and JSON-LD url differ only by slash
    for key, u in sorted(seen.items()):
        code = http_status(u)
        if code != 200:
            bad("anonymous %s for %s (visitors cannot reach it)" % (code, u))

    # 5. star badges vs GitHub
    for href, body in cards:
        sm = re.search(r"(\d+)★", body)
        if not sm:
            continue
        mm = re.search(r"github\.com/([^/]+)/([^/\"?#]+)", href)
        if not mm:
            continue
        owner, repo = mm.group(1), mm.group(2)
        code, n = stars_of(owner, repo)
        if n is None:
            bad("cannot read %s/%s anonymously for star check (%s)" % (owner, repo, code))
        elif n != int(sm.group(1)):
            bad("%s/%s badge says %s★, GitHub says %d★"
                % (owner, repo, sm.group(1), n))

    # 6. "收录近 70 个" claim vs the actual link count in awesome-YuE's README
    claims = [(int(m.group(1)), m.group(0))
              for m in re.finditer(r"收录 (\d+ 余个|近 \d+ 个)", idx)]
    if claims:
        code, readme = _get("https://raw.githubusercontent.com/RevolutionLA/awesome-YuE/HEAD/README.md")
        if code != 200:
            bad("cannot fetch awesome-YuE README anonymously (%s)" % code)
        else:
            links = {"%s/%s" % g for g in re.findall(
                r"github\.com/([^/\"'\s?#]+)/([^/\"'\s?#)]+)",
                readme.decode("utf-8", "replace"))}
            links.discard("RevolutionLA/awesome-YuE")
            n = len(links)
            for base_n, text in claims:
                # "N 余个" means strictly above N, below N+10; "近 N 个" means just under
                ok = (base_n < n < base_n + 10) if "余" in text else (n <= base_n and n > base_n * 0.9)
                if not ok:
                    bad("claim '%s' vs %d unique repo links in awesome-YuE README" % (text, n))

    # 7. contrast of small text (<=14px) on both surfaces it can sit on
    vars_ = dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{3,6})", css))
    surfaces = [v for k, v in vars_.items() if k in ("--space-0", "--space-2")]
    if not surfaces:
        bad("could not resolve page/card surfaces to check contrast")
    for sel, body in re.findall(r"([^{}]+)\{([^}]*)\}", css):
        sel = sel.strip()
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

    # 8. heading levels must not skip
    seq = [int(h) for h in re.findall(r"<h([1-6])[\s>]", idx)]
    for a, b in zip(seq, seq[1:]):
        if b > a + 1:
            bad("heading jumps h%d -> h%d" % (a, b))
            break

    # 9. og:image dimensions must match the declared ones
    mm = re.search(r'og:image:width" content="(\d+)"', idx)
    nn = re.search(r'og:image:height" content="(\d+)"', idx)
    if mm and nn:
        try:
            from PIL import Image
            size = Image.open(os.path.join(root, "assets", "img", "og-image.png")).size
            if size != (int(mm.group(1)), int(nn.group(1))):
                bad("og-image is %dx%d but meta says %sx%s"
                    % (size[0], size[1], mm.group(1), nn.group(1)))
        except ImportError:
            bad("Pillow missing: og-image dimensions unchecked")

    # 10. no stale references to projects that are no longer listed
    for name in ("dsh-mate",):
        if name in idx:
            bad("index.html still references %s" % name)

    return findings


def selftest(root):
    """Mutate a copy; the checker must notice each kind of breakage."""
    import shutil
    import tempfile
    cases = [
        ("count claim", "index.html", "以下 9 个项目", "以下 42 个项目", "prose says"),
        ("dead link", "index.html", "https://github.com/RevolutionLA/shijing",
         "https://github.com/RevolutionLA/thisRepoDoesNotExist-42", "visitors cannot reach"),
        ("sub-threshold small text", "assets/css/styles.css",
         "--ink-faint: #7a8798", "--ink-faint: #5d6878", "text is"),
    ]
    rc = 0
    for label, target, old, new, expect in cases:
        tmp = tempfile.mkdtemp(prefix="claims-selftest-")
        try:
            for rel in ("index.html", "assets/css/styles.css", "assets/img/og-image.png"):
                dst = os.path.join(tmp, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy(os.path.join(root, rel), dst)
            p = os.path.join(tmp, target)
            s = open(p, encoding="utf-8").read()
            if old not in s:
                print("selftest [%s] SKIPPED - anchor text absent" % label)
                rc = 1
                continue
            open(p, "w", encoding="utf-8").write(s.replace(old, new))
            found = check(tmp)
            if any(expect in f for f in found):
                print("selftest OK - caught %s" % label)
            else:
                print("selftest FAILED - blind to %s: %r" % (label, found))
                rc = 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=ROOT)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest(a.root))
    found = check(a.root)
    for f in found:
        print("FAIL  %s" % f)
    print("%d finding(s)" % len(found))
    sys.exit(1 if found else 0)


if __name__ == "__main__":
    main()
