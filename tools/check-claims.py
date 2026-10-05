"""Verify the factual claims the homepage makes, against the live web.

    python tools/check-claims.py [--root DIR] [--offline] [--selftest]

Exit codes are three-state on purpose:
  0  every claim holds (also: --offline ran the static claims cleanly)
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
- "Cannot check" splits in two, and the split decides the colour: the environment not
  answering is inconclusive, but *this script* failing to read a page that answered
  200 is a finding — the watcher broke, and the digits on the page are now unwatched.
- Anything that is not an integer HTTP status is transport, never a verdict. curl
  prints 000 when it could not connect; read as HTTP 0 that became a false dead link.
  The mirror of that bug is a fallback that *invents* an integer (a HEAD path that
  ended in `return 200` while curl was describing a 404) — an observed-looking status
  nobody observed. Every curl payload, GET or HEAD, is parsed by the one function
  `_curl_raw`, so there is no second place to get this wrong.
- `--selftest` mutates a copy and asserts the checker reports it. A check that cannot
  fail is not a check; a selftest that SKIPS cases must not report success either.
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
NPM_PKG = "dsh-dream-skin"
# 页面写的是「发布以来累计」，所以核对的左端点就是发布日；npm 没有 all-time 端点，
# 只有区间端点，累计值由下面的按年分片求和得到。
NPM_SINCE = datetime.date(2026, 8, 15)
NPM_REG = "https://registry.npmjs.org/%s" % NPM_PKG


def npm_range_url(a, b):
    return "https://api.npmjs.org/downloads/range/%s:%s/%s" % (
        a.isoformat(), b.isoformat(), NPM_PKG)


# probe() asks this one: it is the first chunk every cumulative total is built from.
NPM_FIRST_CHUNK = npm_range_url(NPM_SINCE, NPM_SINCE + datetime.timedelta(days=364))


def npm_cumulative(today):
    """Sum npm's daily download counts from the publish date to `today`, inclusive.

    npm has no all-time endpoint, only ranges. One call covers the package today, but
    the walk is chunked at 365 days so the check cannot turn into a false alarm the day
    the package turns one year old - the failure mode would be a red run blaming the
    page for a limit nobody told it about.

    Returns (total, err); err is None, or "unk:<why>" / "bad:<why>" so the caller can
    keep the same three-state verdict discipline as every other network site.
    """
    total, cur = 0, NPM_SINCE
    while cur <= today:
        stop = min(cur + datetime.timedelta(days=364), today)
        url = npm_range_url(cur, stop)
        code, blob = fetch(url)
        if _is_transport(code):
            return None, "unk:cannot fetch %s (%s): npm download claim unchecked" % (url, code)
        if code in THROTTLE:
            return None, "unk:HTTP %d for %s - %s; npm download claim unchecked" % (
                code, url, THROTTLE[code])
        if code != 200:
            return None, "bad:anonymous HTTP %d for %s" % (code, url)
        try:
            doc = json.loads(blob.decode("utf-8", "replace"))
        except ValueError:
            # 200 但读不出数字：是这个脚本瞎了，不是页面在撒谎
            return None, ("bad:npm downloads API returned 200 that is not JSON - the "
                          "download claim is unwatched")
        rows = doc.get("downloads")
        if not isinstance(rows, list):
            return None, ("bad:npm downloads API returned JSON without a 'downloads' "
                          "list (%r) - the download claim is unwatched"
                          % (str(doc)[:120],))
        # 区间端点必须真的被满足：少给一天，累计值就少一天，页面会被判成「虚高」。
        span = (stop - cur).days + 1
        if len(rows) < span:
            return None, ("bad:npm downloads API covered %d of %d days in %s:%s - the "
                          "cumulative total cannot be summed from a partial window"
                          % (len(rows), span, cur, stop))
        total += sum(int(r.get("downloads", 0)) for r in rows)
        cur = stop + datetime.timedelta(days=1)
    return total, None

# GitHub answers anonymous bursts with HTTP 200 + one of these interstitials.
BLOCK_MARKERS = (b"whoa there", b"abuse detection", b"you have been blocked",
                 b"rate limit exceeded")
# Statuses that mean "the server is reacting to US asking", not "this page is dead".
# 429 is GitHub throttling a burst; 502/503 are what this machine's proxy answers when
# its tunnel gives up (it did so repeatedly during these reviews). Reporting any of
# them as "visitors cannot reach it" is the P0-B mistake with a different number in it.
THROTTLE = {429: "throttled by the host (too many anonymous requests)",
            502: "bad gateway - proxy/gateway upstream, not the page",
            503: "temporarily unavailable - host-side, not the page"}
# Every place a network status turns into a verdict, and the phrase only the THROTTLE
# branch can emit. The selftest asserts each one speaks up; a branch that forgets to
# classify the throttle then fails a test instead of shipping a false finding.
THROTTLE_SITES = (
    ("link check", ("HTTP 429", "link unverified")),
    ("star badges", ("anonymously (429)", "badge for")),
    ("awesome-YuE README claim", ("HTTP 429", "'收录' claim unchecked")),
    ("sitemap lastmod", ("HTTP 429", "lastmod")),
    ("npm cumulative downloads", ("HTTP 429", "npm download claim unchecked")),
    ("npm package ownership", ("HTTP 429", "npm package ownership unchecked")),
)
_STATUS_LINE = re.compile(rb"HTTP/[\d.]+ \d{3}")


def _get(url, timeout=25):
    # UA sets Accept-Encoding: identity, so every path below is plain bytes.
    req = urllib.request.Request(url, headers=dict(UA))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        # An HTTPError is also the response object: GitHub's 403 carries
        # {"message":"API rate limit exceeded..."} and without this the
        # checker can only say "no reason given" about a host answer it held.
        try:
            return e.code, e.read()
        except Exception:
            return e.code, b""
    except Exception as e:
        return "err:%s" % (e.reason if isinstance(e, urllib.error.URLError)
                           else type(e).__name__), b""


def _curl_raw(extra, url, timeout=25):
    """The ONE place curl's stdout gets parsed into (status, payload).

    Review P1-新: a second, HEAD-specific copy of this parsing ended in
    `return 200, heads` without ever reading a status line - curl exits 0 for 404
    pages, so the fallback minted a healthy 200 for a dead URL. Same bug family as
    P0-B, mirrored: that one turned "no observation" into an accusation, this one
    turned a real 404 into a clean bill of health. One pattern, one parser.
    """
    if not CURL:
        return None, b""
    try:
        p = subprocess.run(
            [CURL, "-sS", "-L", "-w", "\n%{http_code}", "--max-time", str(timeout)]
            + extra + [url],
            capture_output=True, timeout=timeout + 5)
    except Exception as e:
        return "err:curl-%s" % type(e).__name__, b""
    if p.returncode != 0:
        # 6 = could not resolve, 7 = failed to connect, 35 = TLS, 28 = timeout.
        # None of those is an HTTP status, so none of them may be reported as one.
        return "err:curl-exit-%d" % p.returncode, p.stderr[:120]
    out = p.stdout
    i = out.rfind(b"\n")
    if i < 0:
        return "err:curl-no-status", b""
    code, blob = out[i + 1:].strip(), out[:i]
    if not code.isdigit() or int(code) == 0:
        # curl prints 000 for "no HTTP response at all" even with exit status 0;
        # int("000") == 0 would otherwise reach the link check and read as a dead link.
        return "err:curl-code-%s" % code.decode("ascii", "replace"), blob
    return int(code), blob


def _curl_get(url, timeout=25):
    """curl follows redirects and survives the resets this machine's proxy throws
    at python-urllib."""
    return _curl_raw([], url, timeout)


def _curl_head(url, timeout=25):
    """HEAD via curl: the status comes from the same parser as everything else,
    so a 404 can never arrive wearing a 200."""
    code, blob = _curl_raw(["-I"], url, timeout)
    if _is_transport(code):
        return code, None
    heads = {}
    for line in blob.split(b"\n"):
        if _STATUS_LINE.match(line.strip()):
            # -L dumps every hop: a Last-Modified carried from a redirect we did not
            # settle on would vouch for a date this URL never reported. Match is
            # start-anchored because HTTP/1.1 appends a reason phrase.
            heads = {}
            continue
        k, sep, v = line.partition(b":")
        if sep and k.strip():
            heads[k.strip().lower().decode("ascii", "replace")] = v.strip().decode(
                "ascii", "replace")
    return code, heads


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
    """Anything that is not an HTTP status code is a transport problem.

    Defined by exclusion rather than by matching "err:" so a new fallback path
    leaking a stray value degrades to inconclusive instead of to an accusation.
    (Review 2026-09-27 P0-B: curl's `000` parsed to int 0 and was reported as
    'visitors cannot reach it' - exactly the false alarm this script exists to stop.)
    """
    return not (isinstance(code, int) and 100 <= code <= 599)


def _parse_lm(raw):
    if not raw:
        return None
    try:
        # Pages stamps UTC; the sitemap date is a local calendar date. Comparing the
        # two raw makes every push before 08:00 local look a day ahead of itself.
        dt = parsedate_to_datetime(raw)
        return dt.astimezone().date() if dt.tzinfo else dt.date()
    except (TypeError, ValueError):
        return None


def _head_headers(url, timeout=25):
    """HEAD with urllib, falling back to curl -I: this machine's proxy resets urllib
    on its own, and five 'lastmod unverified' lines are not a pass (review P1-C).

    The fallback must not invent a status. Review P1-新: it used to parse the header
    dump itself and end in `return 200, heads`, and `curl -sSIL` exits 0 for a 404
    page - so every URL this branch ever saw was reported healthy. That is the worst
    kind of value for this script to produce: an integer status that was never
    observed. It now defers to _curl_head, which parses %{http_code} like every
    other curl call.
    """
    req = urllib.request.Request(url, headers=dict(UA), method="HEAD")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers
    except Exception as e:
        code = "err:%s" % (e.reason if isinstance(e, urllib.error.URLError)
                            else type(e).__name__)
    if not CURL:
        return code, None
    return _curl_head(url, timeout)


def last_modified(url):
    """Pages' own Last-Modified header - the only date a deployed page can vouch for.
    Returns (code, date_or_None). urllib gives a Message (its .get() is already
    case-insensitive), _curl_head gives a dict keyed in lowercase; one lookup fits both."""
    code, headers = _head_headers(url)
    if _is_transport(code) or headers is None:
        return code, None
    return code, _parse_lm(headers.get("last-modified"))


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


def _media_block(css, cond):
    """Return the body of the first @media block whose prelude contains `cond`.

    Brace-counted rather than regexed: `[^}]*` stops at the first nested rule, so a
    regex would report "the block does not hide .nav-links" for any block that has
    one rule inside it - a check that passes by reading nothing.
    """
    i = css.find("@media")
    while i >= 0:
        j = css.index("{", i)
        if cond in css[i:j]:
            depth, k = 0, j
            while k < len(css):
                if css[k] == "{":
                    depth += 1
                elif css[k] == "}":
                    depth -= 1
                    if depth == 0:
                        return css[j + 1:k]
                k += 1
            return ""
        i = css.find("@media", i + 6)
    return None


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

    # Normalise once: a legal-but-malformed ListItem (missing/non-object `item`) must
    # produce a finding, not a KeyError stack trace (review P2-B).
    its = []
    for i, el in enumerate(items):
        it = el.get("item") if isinstance(el, dict) else None
        if not isinstance(it, dict):
            bad("ItemList element %d carries no object-valued 'item' - "
                "its claims cannot be checked" % (i + 1))
            it = {}
        its.append(it)

    # ---------- static: 「按 star 从高到低排」是一句可机检的话 ----------
    # 文案一旦这么写，卡片在文档顺序里的星数就必须单调不增。
    # 这条不看网络：它查的是「页面自己说的话自不自相」；线上真实星数由下面的
    # star badge 检查负责。两者分开，才分得清「排序错了」和「数字过期了」。
    ladder = []
    for href, body in cards:
        nm = re.search(r'<h4 class="pc-name">(.*?)</h4>', body)
        sm = re.search(r'(\d+)★', body)
        if sm:
            ladder.append((nm.group(1) if nm else "?", int(sm.group(1))))
        elif "★" in body:
            # 「10+★」「约 10★」这种写法会让下面两条检查同时看不见这张卡：
            # 排名不查了，线上星数也不查了。写得读不懂的徽章必须是 finding，不是盲区。
            bad("card '%s' wears a ★ badge the checker cannot parse (%r) - the ranking "
                "and the live star count both go blind on it"
                % (nm.group(1) if nm else "?", body[max(0, body.index("★") - 14):body.index("★") + 1]))
    # 名次徽章 01..NN 本身就是一句「从高到低」，所以这条不依赖文案有没有写：
    # 删掉那句 prose 不该让排序检查一起消失（review：checker blind spot）。
    if len(ladder) >= 2:
        for (a, av), (b, bv) in zip(ladder, ladder[1:]):
            if bv > av:
                bad("the cards are presented as a star-descending ladder (ordered by stars "
                    "从高到低), but '%s' (%d★) is listed above '%s' (%d★)" % (a, av, b, bv))
                break
    elif "从高到低" in idx:
        bad("prose claims a star-descending ranking but only %d card(s) carry a readable "
            "star badge - there is no ladder to check" % len(ladder))

    # 名次编号：01..NN 连续，且与卡数一致——复制粘贴漏改最直接的表现
    ranks = [int(r) for r in re.findall(r'<i class="pc-rank">(\d{2})</i>', idx)]
    if not ranks:
        bad("no pc-rank badges anywhere - the rank-ladder check has gone blind")
    elif len(ranks) != len(cards):
        bad("%d rank badges for %d cards - every card must carry its place in the ladder"
            % (len(ranks), len(cards)))
    elif ranks != list(range(1, len(ranks) + 1)):
        bad("rank badges read %s, not a clean 01..%02d ladder over %d cards"
            % (ranks, len(ranks), len(cards)))

    # ---------- static: 关于区那排数字里，「项目」那一个就是本页的卡数 ----------
    # 它写的是「精选」，不是「我一共多少个仓库」——主页上的仓库数一直在涨，那个数字
    # 在这儿既查不了也不该拿来当门面；查得了的是它和页面自己列出的卡对不对得上。
    # data-count 和正文各写一个数也不行：计数动画会把正文盖成 data-count 那个。
    strip = re.findall(r'<li><b data-count="(\d+)">(\d+)</b>'
                       r'<span class="mono">([^<]*)</span></li>', idx)
    proj = [(a, b, lbl) for a, b, lbl in strip if lbl.endswith("项目")]
    if not proj:
        bad("no 项目 counter in .stat-strip - the headline number and the card count can "
            "now drift apart with nobody watching")
    else:
        anim, shown, lbl = proj[0]
        if anim != shown:
            bad("the stat strip's %s counter reads %s but counts up to %s - the animation "
                "overwrites the one the reader sees first" % (lbl, shown, anim))
        elif int(shown) != len(cards):
            bad("the stat strip says %s %s but the page carries %d project cards"
                % (shown, lbl, len(cards)))

    # ---------- static: 筛选开关上的数字要数得对 ----------
    at = idx.find('class="proj-rest"')
    if at < 0:
        bad("no .proj-rest region found - the filter-count check has nothing to count")
    else:
        rest = idx[at:idx.index("</section>", at)]
        cats = re.findall(r'data-cat="([^"]+)"', rest)
        chips = re.findall(r'data-filter="([^"]+)"[^>]*>[^<]*<b>(\d+)</b>', rest)
        if not chips:
            bad("no filter chips inside .proj-rest - the filter-count check is blind")
        for f, n in chips:
            want = len(cats) if f == "all" else cats.count(f)
            if int(n) != want:
                bad("filter chip '%s' says %s but %d card(s) in .proj-rest match it"
                    % (f, n, want))
            elif f != "all" and want == 0:
                # 一个 0 张卡命中的开关写「00」也是「数得对」的，但它只会让人点了全空
                bad("filter chip '%s' matches no card in .proj-rest - it hides everything "
                    "when clicked" % f)
        if not any(f == "all" for f, _ in chips):
            bad("no ALL chip in .proj-rest - visitors cannot get back to the full list")
        # 分组标题上写的「08」是给人看的数量，和徽章一样会漏改
        head = re.search(r'pg-label">RANKED[^<]*</span>.*?pg-count">(\d+)</span>', idx, re.S)
        if not head:
            bad("no RANKED group head with a pg-count - the ladder size is unstated")
        elif int(head.group(1)) != len(cats):
            bad("the RANKED head counts %s projects but .proj-rest renders %d cards"
                % (head.group(1), len(cats)))

    # ---------- static: name -> repo map, taken from sameAs (authoritative) ----------
    repo_by_name = {}
    for it in its:
        mm = re.search(r"github\.com/([^/]+)/([^/\"?#]+)", str(it.get("sameAs", "")))
        if mm and it.get("name"):
            repo_by_name[it["name"]] = mm.groups()

    card_names = [m.group(1) for _, b in cards for m in [re.search(r'<h4 class="pc-name">(.*?)</h4>', b)] if m]
    for it in its:
        if it.get("name") and it["name"] not in card_names:
            bad("ItemList entry '%s' has no card on the page" % it["name"])

    # ---------- static: 结构化数据的「顺序」也得和页面一致 ----------
    # 只比成员集合，等于允许访客看到的排序和机器读到的排序是两套。
    if il:
        order = str(il.get("itemListOrder", ""))
        if "Descending" not in order:
            bad("ItemList itemListOrder is %r while the page ranks its projects by stars "
                "从高到低 - structured data states the opposite of the prose"
                % (order or "(unset)",))
        pos = [el.get("position") for el in items if isinstance(el, dict)]
        if pos != list(range(1, len(items) + 1)):
            bad("ItemList positions read %s, not 1..%d in list order" % (pos, len(items)))
        names = [it.get("name") for it in its]
        if names != card_names:
            bad("ItemList order %s is not the order the cards appear in %s"
                % (names, card_names))

    # ---------- static: card <-> ItemList, and case-exact href mapping ----------
    urls = {it.get("url", "") for it in its}
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

    # npm 累计下载量：先确认声明还在，再决定去不去核数
    dl = re.search(r"累计 <b>([\d,]+)</b> 次下载", idx)
    dl_claim = None
    if not dl:
        bad("no '累计 N 次下载' claim found - the npm download check has gone blind again")
    else:
        dl_claim = int(dl.group(1).replace(",", ""))

    # ---------- static: 窄屏导航必须真的能走通 ----------
    # Four review rounds carried the same item: below 760px the main nav was
    # `display: none` with nothing to replace it, so a phone visitor could only
    # scroll. "Hidden" is fine; "hidden and unreachable" is the defect. This checks
    # the reachable-path invariant, not the pixels: a future edit that re-hides the
    # list behind an unconditional rule, or points the toggle at a stale id, fails.
    mobile = _media_block(css, "max-width: 760px")
    if mobile is None:
        unk("no 'max-width: 760px' block in styles.css - the mobile-nav check has "
            "nothing to read")
    else:
        nav_list = re.search(r'<ul class="nav-links"[^>]*id="([^"]+)"(.*?)</ul>',
                             idx, re.S)
        anchors = re.findall(r'href="#([^"]+)"', nav_list.group(2)) if nav_list else []
        if not anchors:
            bad("no in-page anchors found in ul.nav-links - the mobile-nav check has "
                "gone blind again")
        page_ids = set(re.findall(r'\bid="([^"]+)"', idx))
        dangling = [a for a in anchors if a not in page_ids]
        if dangling:
            bad("nav links point at ids that are not on the page: %s"
                % ", ".join("#" + a for a in dangling))
        if not nav_list:
            bad("ul.nav-links has no id - nothing can be wired to the section list")
        else:
            toggle = re.search(r'<button[^>]*aria-controls="%s"[^>]*>' % nav_list.group(1),
                               idx)
            if not toggle:
                bad("no aria-controls=%s button: the section list is unreachable below "
                    "760px" % nav_list.group(1))
            elif 'aria-expanded' not in toggle.group(0):
                bad("the nav toggle has no aria-expanded - a screen reader cannot tell "
                    "whether the menu is open")
        # Comments are prose, not selectors. Left in, a sentence that happens to
        # mention `.js` or `display: none` flips the two verdicts below - which is
        # exactly how this check first shipped, green and reading nothing.
        rules = re.findall(r'([^{}]+)\{([^{}]*)\}', re.sub(r"/\*.*?\*/", "", mobile, flags=re.S))
        hidden = [sel.strip() for sel, body in rules
                  if re.search(r'\.nav-links\b', sel)
                  and "display:none" in re.sub(r"\s+", "", body)]
        ungated = [s for s in hidden if ".js" not in s]
        if ungated:
            bad("mobile nav hidden with no JS gate (%s): without script the section "
                "links are simply gone" % " / ".join(ungated))
        shown = [body for sel, body in rules
                 if re.search(r'\.nav-toggle\b', sel)
                 and "display:none" not in re.sub(r"\s+", "", body)]
        if hidden and not shown:
            bad("mobile CSS hides .nav-links but never shows .nav-toggle - the menu "
                "has no way to open")
        # The drawer is a two-file agreement: CSS keys off a class, JS toggles it.
        # Rename it on one side only and every check above still passes while the
        # menu quietly stops opening, so compare the tokens instead of trusting them.
        js_ref = re.search(r'<script src="([^"]*main\.js)"', idx)
        if not js_ref:
            bad("index.html no longer loads a *main.js - the nav wiring is unchecked")
        else:
            js_path = os.path.join(root, js_ref.group(1).replace("/", os.sep))
            if not os.path.exists(js_path):
                bad("index.html loads %s which does not exist on disk" % js_ref.group(1))
            else:
                js = open(js_path, encoding="utf-8").read()
                css_open_cls = re.findall(r"\.site-header(\.[\w-]+)\s+\.nav-links", mobile)
                if not css_open_cls:
                    bad("no '.site-header.<class> .nav-links' rule in the mobile block - "
                        "the nav-open check has gone blind again")
                for cls in dict.fromkeys(css_open_cls):
                    # The CSS writes it as a selector (`.nav-open`), the JS as a class
                    # name ('nav-open') - compare the name, not the dotted form.
                    if cls[1:] not in js:
                        bad("CSS opens the drawer on .%s but %s never toggles it - the "
                            "menu cannot open" % (cls[1:], js_ref.group(1)))
                # A drawer a screen reader can't see the state of, or a keyboard user
                # can't get out of, is the same defect that hid the nav for four rounds.
                if 'aria-expanded' not in js:
                    bad("%s never updates aria-expanded - assistive tech reports the "
                        "drawer as always closed" % js_ref.group(1))
                if "key === 'Escape'" not in js:
                    bad("%s has no Escape path - the drawer traps keyboard focus"
                        % js_ref.group(1))

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
        # A deliberate skip is not "inconclusive": exit 2 here would make --offline
        # useless as a CI gate (always red) or the gate blind (2 treated as pass).
        return findings, unknown

    # ---------- network: every public URL must answer 200 to an anonymous visitor ----
    anon = set(h for h, _ in cards) | set(sm_locs)
    for it in its:
        for k in ("url", "sameAs"):
            v = it.get(k)
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
        elif code in THROTTLE:
            # The server is complaining about US, not about the link. Calling a 429
            # "visitors cannot reach it" is the same mistake as calling a proxy reset
            # one - and it fires in bursts, because this loop makes a dozen requests.
            unk("HTTP %d for %s - %s; link unverified, re-run later"
                % (code, u, THROTTLE[code]))
        elif code != 200:
            bad("anonymous %s for %s (visitors cannot reach it)" % (code, u))

    # ---------- network: sitemap lastmod must be backed by the deployed page ----------
    # Pages stamps Last-Modified at build time, so it is the only date a live URL can
    # vouch for. Writing this sitemap without that check put 3 of 4 dates a month ahead.
    # Tolerance is +-1 day on purpose: the header is UTC while a hand-written lastmod is
    # a local calendar date, so the two conventions legitimately differ by a day
    # (review P2-A: three entries said 08-23 where the page said 08-23 GMT / 08-24 local).
    for loc, declared_s in sm_dates:
        declared = datetime.date.fromisoformat(declared_s)
        code, live = last_modified(loc)
        time.sleep(0.2)
        if _is_transport(code):
            unk("cannot read Last-Modified for %s (%s)" % (loc, code))
        elif code in THROTTLE:
            # Review P3: without this the run says "no Last-Modified header", blaming
            # the page for a header we were throttled out of reading. Same wrong
            # attribution as the 429-as-dead-link family, one branch over.
            unk("HTTP %d for %s - %s; lastmod %s unverified, re-run later"
                % (code, loc, THROTTLE[code], declared_s))
        elif live is None:
            unk("no Last-Modified header for %s; lastmod %s unverified" % (loc, declared_s))
        elif abs((declared - live).days) > 1:
            bad("sitemap says %s lastmod %s, but the deployed page reports %s"
                % (loc, declared_s, live))
        elif declared > live:
            unk("%s lastmod %s is ahead of the deployed page (%s) - ok only if a push follows"
                % (loc, declared_s, live))

    # ---------- network: star badges against the live repo page ----------
    for name, (owner, r), claimed in star_claims:
        code, n, state = stars_of(owner, r)
        time.sleep(0.3)
        if state == "blocked":
            # Environment, not the page and not the scraper: say which, or the reader
            # goes to fix markup that never changed (review P2-4).
            unk("github answered 200 with a block/abuse page for %s/%s - anonymous rate "
                "limit, badge for '%s' unchecked; re-run later" % (owner, r, name))
        elif state == "unparsed":
            # The page answered, we just can no longer read the number out of it.
            # That is this script breaking, not the environment - and those digits on
            # the page are now unwatched, which is exactly how 187 reached production.
            bad("star counter not found in github.com/%s/%s (HTTP 200) - the scraper is "
                "blind and the %d★ badge for '%s' is unwatched" % (owner, r, claimed, name))
        elif state != "ok":
            unk("cannot read %s/%s anonymously (%s), badge for '%s' unchecked"
                % (owner, r, code, name))
        elif n != claimed:
            bad("%s/%s badge says %d★, GitHub says %d★" % (owner, r, claimed, n))

    # ---------- network: "收录近 70 个" vs the README's real link count ----------
    if claims:
        code, readme = fetch(YUE_README)
        if _is_transport(code):
            unk("cannot fetch awesome-YuE README (%s): '收录' claim unchecked" % code)
        elif code in THROTTLE:
            unk("HTTP %d for %s - %s; '收录' claim unchecked, re-run later"
                % (code, YUE_README, THROTTLE[code]))
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

    # ---------- network: npm 累计下载量（只会涨，所以判法与滑窗不同） ----------
    if dl_claim is not None:
        live, err = npm_cumulative(datetime.datetime.now(datetime.timezone.utc).date())
        if err:
            kind, msg = err.split(":", 1)
            (unk if kind == "unk" else bad)(msg)
        elif dl_claim > live:
            # 累计数虚高没有「窗口滑动」可以甩锅：它就是把别人的量算成了此刻的量。
            bad("page says {:,} cumulative npm downloads, npm says {:,} since {} "
                "- the page overstates the total by {:,}".format(
                    dl_claim, live, NPM_SINCE, dl_claim - live))
        elif live - dl_claim > 0.10 * live:
            # 落后 10% ≈ 这包目前五天的量。数字没撒谎，但它已经在替过去说话了。
            bad("page says {:,} cumulative npm downloads, npm says {:,} today "
                "- the number on the page is {:,} downloads behind".format(
                    dl_claim, live, live - dl_claim))
        # 归属核对：这个包得真的是那个仓库的，否则数字再对也不是「我的」下载量
        code2, reg = fetch(NPM_REG)
        if _is_transport(code2):
            unk("cannot fetch %s (%s): npm package ownership unchecked" % (NPM_REG, code2))
        elif code2 in THROTTLE:
            # 被限流是这台机器的处境，不是页面的错误——和上面每一条判定出口一样，
            # 只能记 inconclusive，否则一次 429 就会被读成「访客打不开这个包」。
            unk("HTTP %d for %s - %s; npm package ownership unchecked"
                % (code2, NPM_REG, THROTTLE[code2]))
        elif code2 != 200:
            bad("anonymous %s for %s - the page credits npm downloads to a package "
                "that does not answer" % (code2, NPM_REG))
        else:
            try:
                doc2 = json.loads(reg.decode("utf-8", "replace"))
                repo_url = (doc2.get("repository") or {}).get("url", "")
            except ValueError:
                bad("npm registry returned 200 that is not JSON - package ownership "
                    "cannot be confirmed")
                repo_url = ""
            if repo_url and "RevolutionLA/dsh-dream-skin" not in repo_url:
                bad("npm package dsh-dream-skin belongs to %r, not the repo the page "
                    "links" % repo_url)
            elif not repo_url:
                bad("npm package dsh-dream-skin declares no repository - the download "
                    "count cannot be attributed to it")

    return findings, unknown


def stars_of(owner, repo):
    """Scrape the public repo page instead of api.github.com: the API is capped at
    60 anonymous calls/hour, which turns a passing run into a flaky failure.

    Three-state on purpose. "the host is down" is the environment's problem;
    "the page arrived but I cannot read the number" is THIS script being broken, and
    must not hide behind an inconclusive exit code.

    A third state exists because the two above are not exhaustive: GitHub answers
    anonymous bursts with HTTP 200 and an abuse/block page (review P2-4). To a regex
    that is indistinguishable from "GitHub changed the markup", but the two demand
    opposite actions - one says fix the page, the other says wait. Guessing wrong
    here sends whoever reads the output after the wrong fixer.
    """
    code, body = fetch("https://github.com/%s/%s" % (owner, repo))
    if _is_transport(code):
        return code, None, "transport"
    if code != 200:
        return code, None, "http-%s" % code
    low = body.lower()
    if b"repo-stars-counter-star" not in low and any(m in low for m in BLOCK_MARKERS):
        return 200, None, "blocked"
    m = re.search(r'repo-stars-counter-star[^>]*title="([\d,]+)"',
                  body.decode("utf-8", "replace"))
    return (200, int(m.group(1).replace(",", "")), "ok") if m else (200, None, "unparsed")


CASES = [
    # label, target file, old, new, expect, dependency, mode
    # dependency: None = pure static; "github" = github.com HTML pages;
    # "raw" = raw.githubusercontent.com; "pages" = this site as deployed on Pages
    # mode: "finding"  -> some finding must contain `expect`
    #       "transport"-> no finding may accuse the mutated URL of being dead, and
    #                      an inconclusive must mention it (a refusal to guess)
    ("project count claim", "index.html", "以下 9 个项目", "以下 42 个项目", "prose says", None, "finding"),
    ("dead link", "index.html", "https://github.com/RevolutionLA/shijing",
     "https://github.com/RevolutionLA/thisRepoDoesNotExist-42", "visitors cannot reach", "github", "finding"),
    ("sub-threshold small text", "assets/css/styles.css",
     "--ink-faint: #8290a2", "--ink-faint: #5d6878", "text is", None, "finding"),
    ("stale star badge", "index.html", "开源工作站 · 4★", "开源工作站 · 999★", "badge says", "github", "finding"),
    ("star badge the checker cannot cover", "index.html",
     '<h4 class="pc-name">adversarial-review</h4>', '<h4 class="pc-name">adversarial-reviewx</h4>',
     "no repo in ItemList sameAs", None, "finding"),
    ("false 收录 claim", "index.html", "收录近 70 个", "收录近 900 个", "claim '", "raw", "finding"),
    ("收录 claim removed", "index.html", "收录近 70 个", "收录一批", "gone blind", None, "finding"),
    # the mobile-nav invariant, proven able to fail: hide the list the way the page
    # did for four review rounds and the checker must say so
    ("mobile nav hidden with no toggle", "index.html",
     'aria-controls="nav-links"', 'aria-controls="nowhere"',
     "unreachable below 760px", None, "finding"),
    ("mobile nav hidden ungated", "assets/css/styles.css",
     "  .js .nav-links { display: none; }", "  .nav-links { display: none; }",
     "no JS gate", None, "finding"),
    ("nav link points at a missing section", "index.html",
     '<li><a href="#journey">TRACE</a></li>', '<li><a href="#trombone">TRACE</a></li>',
     "ids that are not on the page", None, "finding"),
    ("drawer class renamed in CSS only", "assets/css/styles.css",
     ".site-header.nav-open .nav-links", ".site-header.navopened .nav-links",
     "never toggles it", None, "finding"),
    ("drawer state never announced", "assets/js/main.js",
     "    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');\n", "",
     "never updates aria-expanded", None, "finding"),
    # 锚在 PRINCIPAL 那一组：它前面是 h2 区块标题，改成 h5 才真的构成跳级。
    # （后面那几组的 h3 前面已经排着 h4 卡名，h4->h5 不算 skip，锚在那儿等于没测。）
    ("heading skip", "index.html",
     '<h3 class="pg-head mono"><span class="pg-label">PRINCIPAL · 主打</span>'
     '<span class="pg-rule" aria-hidden="true"></span><span class="pg-count">01</span></h3>',
     '<h5 class="pg-head mono"><span class="pg-label">PRINCIPAL · 主打</span>'
     '<span class="pg-rule" aria-hidden="true"></span><span class="pg-count">01</span></h5>',
     "heading jumps", None, "finding"),
    ("og size mismatch", "index.html", 'og:image:width" content="1200"',
     'og:image:width" content="1201"', "og-image is", None, "finding"),
    ("sitemap repo name wrong case", "sitemap.xml", "/AscendMate/", "/ascendmate/",
     "case-sensitive", None, "finding"),
    ("sitemap lastmod ahead of the deployed page", "sitemap.xml",
     "<lastmod>2026-08-24</lastmod>", "<lastmod>2026-09-26</lastmod>",
     "deployed page reports", "pages", "finding"),
    ("unreachable host must not read as a dead link", "index.html",
     "https://github.com/RevolutionLA/shijing", "https://this-host-does-not-exist-42abc.invalid/",
     "this-host-does-not-exist-42abc.invalid", "github", "transport"),
    ("malformed ListItem must be a finding, not a crash", "index.html",
     '"item": {', '"items": {',
     "carries no object-valued 'item'", None, "finding"),
    # 排名榜的三条：顺序、名次编号、开关计数，都是纯静态就能判的
    ("star ranking claim broken", "index.html", "精选清单 · 10★", "精选清单 · 1★",
     "ordered by stars", None, "finding"),
    ("rank badge out of order", "index.html", '<i class="pc-rank">05</i>',
     '<i class="pc-rank">09</i>', "rank badges", None, "finding"),
    ("filter chip count drift", "index.html", "AI · 音乐 <b>02</b>",
     "AI · 音乐 <b>07</b>", "filter chip", None, "finding"),
    ("npm download claim removed", "index.html", "累计 <b>54,347</b> 次下载",
     "很多人装", "gone blind", None, "finding"),
    ("npm cumulative claim overstated", "index.html", "累计 <b>54,347</b> 次下载",
     "累计 <b>9,999,999</b> 次下载", "overstates the total", "npm", "finding"),
    ("npm cumulative claim left behind", "index.html", "累计 <b>54,347</b> 次下载",
     "累计 <b>1,000</b> 次下载", "behind", "npm", "finding"),
    # 这一组测的是「检查本身会不会变瞎」：每条都制造一个盲区或一处自相矛盾，
    # 而矛盾恰好落在刚加固过的那几条守卫上（pg-count / 名次徽章 / ★ 徽章 / ItemList 顺序）。
    ("RANKED head count drift", "index.html", '<span class="pg-count">08</span>',
     '<span class="pg-count">07</span>', "RANKED head counts", None, "finding"),
    ("a card loses its rank badge", "index.html", '<i class="pc-rank">05</i>',
     '<i class="pc-place">05</i>', "every card must carry", None, "finding"),
    ("star badge written in a way nobody can read", "index.html",
     '<i class="pc-rank">09</i>● 数据小品 · 0★',
     '<i class="pc-rank">09</i>● 数据小品 · 200+★', "cannot parse", None, "finding"),
    ("structured data says ascending", "index.html", "ItemListOrderDescending",
     "ItemListOrderAscending", "itemListOrder", None, "finding"),
    ("ListItem position swapped", "index.html", '"position": 2,', '"position": 3,',
     "ItemList positions", None, "finding"),
    ("filter chip that hides everything", "index.html",
     'data-filter="hardware" aria-pressed="false">开源硬件 <b>01</b>',
     'data-filter="gaming" aria-pressed="false">游戏模组 <b>00</b>',
     "matches no card", None, "finding"),
    ("stat strip counter drifts from the card count", "index.html",
     '<li><b data-count="9">9</b><span class="mono">精选项目</span></li>',
     '<li><b data-count="12">12</b><span class="mono">精选项目</span></li>',
     "the stat strip says 12", None, "finding"),
    ("stat strip counter renamed out of reach", "index.html",
     '<li><b data-count="9">9</b><span class="mono">精选项目</span></li>',
     '<li><b data-count="9">9</b><span class="mono">个仓库</span></li>',
     "no 项目 counter", None, "finding"),
    ("stat strip text and count-up disagree", "index.html",
     '<li><b data-count="9">9</b><span class="mono">精选项目</span></li>',
     '<li><b data-count="9">7</b><span class="mono">精选项目</span></li>',
     "counts up to", None, "finding"),
]


def probe(dep):
    """Which external host this case actually needs. Asserting on the real dependency,
    not on 'is the internet up', is what keeps a skip honest instead of a false pass.

    Measures REACHABILITY only, never a parsed value: if it probed the star scraper,
    a broken scraper would SKIP the star cases and still exit 0 (review P1-B)."""
    if dep == "github":
        return fetch("https://github.com/RevolutionLA/adversarial-review")[0] == 200
    if dep == "raw":
        return fetch(YUE_README)[0] == 200
    if dep == "pages":
        return last_modified("https://revolutionla.github.io/")[0] == 200
    if dep == "npm":
        # 累计值的判定来自区间端点，不是 registry：registry 答 200 而区间端点被限流，
        # 这一轮仍然没有看到下载量。probe 必须问那个真正决定结论的 URL。
        return fetch(NPM_FIRST_CHUNK)[0] == 200
    return True


# A unit case that could not run on this host says so with this prefix, so the runner
# counts it as a skip (exit 2) instead of a pass. Review P2-3: without it, the P0-B
# regression silently reported OK on a machine with no curl - it had nothing to test.
SKIP = "SKIP:"


def _unit_transport_table():
    """0 / None / stray strings are not HTTP statuses. Review P0-B: curl prints 000
    when it cannot connect at all, int() made it a verdict, and the link check called
    the local proxy's hiccup 'visitors cannot reach it'."""
    bad_values = [0, None, "", "err:URLError", "err:curl-exit-7", 5, 600, True]
    wrong = [repr(v) for v in bad_values if not _is_transport(v)]
    good = [repr(v) for v in (200, 301, 403, 404, 500, 599) if _is_transport(v)]
    if wrong or good:
        return "_is_transport misclassifies %s%s" % (wrong, ("as verdicts: " + str(good)) if good else "")
    return None


def _unit_curl_000():
    """Deterministic P0-B regression: an unresolvable host must come back as transport,
    whatever the machine's network looks like at the time."""
    if not CURL:
        return SKIP + " no curl on this host - the case did not run"
    code, _ = _curl_get("https://this-host-does-not-exist-42abc.invalid/")
    if not _is_transport(code):
        return "_curl_get returned %r for a host that cannot be resolved (000 leaked as a status?)" % (code,)
    return None


def _unit_star_parser_blindness():
    """P1-A: a 200 page whose markup we no longer recognise is the scraper breaking."""
    global fetch
    saved = fetch
    try:
        fetch = lambda url, retries=2: (200, b"<html><body>changed markup</body></html>")
        code, n, state = stars_of("RevolutionLA", "shijing")
    finally:
        fetch = saved
    if state != "unparsed":
        return "stars_of on an unparseable 200 page said %r, expected 'unparsed' (would be filed as inconclusive)" % state
    if n is not None:
        return "stars_of invented %r out of a page with no star counter" % (n,)
    return None


def _unit_head_status_is_observed():
    """Deterministic P1-新 regression: when urllib is down, the curl HEAD fallback must
    report the status curl actually saw - never a minted 200.

    Stubbed at the subprocess boundary, not at _curl_head, so the real parser runs; and
    with a canned payload, so this case fires on a machine with no network at all.
    """
    if not CURL:
        return SKIP + " no curl on this host - the fallback this case tests did not run"
    payload = (b"HTTP/1.1 301 Moved Permanently\r\n"
               b"location: https://revolutionla.github.io/not-here-42/\r\n"
               b"last-modified: Mon, 01 Jan 2024 00:00:00 GMT\r\n\r\n"
               b"HTTP/1.1 404 Not Found\r\ncontent-type: text/html\r\n"
               b"server: GitHub.com\r\n\r\n404")
    saved_run, saved_open = subprocess.run, urllib.request.urlopen

    class Fake(object):
        returncode = 0
        stdout = payload
        stderr = b""

    try:
        subprocess.run = lambda *a, **k: Fake()
        urllib.request.urlopen = _raise_reset
        code, heads = _head_headers("https://revolutionla.github.io/not-here-42/")
        if code != 404:
            return "curl HEAD fallback reported %r for a page whose status line says 404 " \
                   "(a minted 200 is an integer that was never observed)" % (code,)
        if not heads or heads.get("server") != "GitHub.com":
            return "curl HEAD fallback parsed headers wrong: %r" % (heads,)
        # The 404 page reports no date of its own; a date in this dict came from the
        # redirect hop, and last_modified() would hand it to the sitemap guard as if
        # the deployed page had vouched for it.
        if "last-modified" in heads or "location" in heads:
            return "curl HEAD kept a header from the 301 hop it did not settle on: %r" % (heads,)

        # And the same path must not turn "no status line" into a verdict either.
        Fake.stdout = b"HTTP/1.1 200 OK\r\ncontent-length: 0\r\n\r\n000"
        code2, _ = _head_headers("https://revolutionla.github.io/not-here-42/")
        if not _is_transport(code2):
            return "curl HEAD fallback turned a missing response (%r) into status %r" % (Fake.stdout, code2)
    finally:
        subprocess.run, urllib.request.urlopen = saved_run, saved_open
    return None


def _raise_reset(*a, **k):
    raise urllib.error.URLError("simulated proxy reset")


def _unit_httperror_body_survives():
    """Regression for the message the checker shows when GitHub throttles us.

    An HTTPError IS the response. Dropping its body is why a 403 could only be
    reported as "no reason given" while the host had said "API rate limit
    exceeded" - and an inconclusive verdict has to name the reason it is one.
    """
    import io
    saved = urllib.request.urlopen
    body = b'{"message":"API rate limit exceeded for 1.2.3.4."}'
    state = {"readable": True}

    class Dead(object):
        def read(self):
            raise OSError("connection reset mid-body")

        def close(self):
            pass

    def fake(req, timeout=None):
        raise urllib.error.HTTPError("https://api.github.com/x", 403, "Forbidden",
                                     {"content-type": "application/json"},
                                     io.BytesIO(body) if state["readable"] else Dead())

    try:
        urllib.request.urlopen = fake
        code, got = fetch("https://api.github.com/x")
        if code != 403:
            return "HTTPError path lost the status: %r" % (code,)
        if b"rate limit" not in got:
            return "HTTPError path threw away the body the host sent (%r) - " \
                   "the verdict would have to say 'no reason given'" % (got,)

        # An error response whose body cannot be read must still report the status
        # it saw; losing the status too would turn this into a transport error and
        # a dead link would be excused for a reason the host never gave.
        state["readable"] = False
        code2, got2 = fetch("https://api.github.com/x")
        if code2 != 403 or got2:
            return "unreadable error body turned into (%r, %r) instead of (403, b'')" % (code2, got2)
    finally:
        urllib.request.urlopen = saved
    return None


def _unit_blocked_page_is_not_blindness():
    """Deterministic P2-4 regression: GitHub's 200 abuse interstitial must read as
    'the environment is throttling us', not as 'the scraper broke'."""
    global fetch
    saved = fetch
    try:
        fetch = lambda url, retries=2: (
            200, b"<html><h1>Whoa there!</h1><p>You have triggered an abuse detection "
                 b"mechanism.</p></html>")
        code, n, state = stars_of("RevolutionLA", "shijing")
    finally:
        fetch = saved
    if state != "blocked":
        return "stars_of read GitHub's block page as %r - a rate limit would be filed as " \
               "a broken scraper and send the reader to fix markup" % (state,)
    if n is not None:
        return "stars_of found %r stars on a block page" % (n,)
    return None


def _unit_probe_asks_the_endpoint_that_matters():
    """probe() must answer about the URL a verdict needs, not a healthy neighbour.

    This first existed for the compare API, whose probe checked /rate_limit - a URL
    that answers 200 while the call the verdict depends on 403s. That made a
    throttled run accuse the checker of going blind when the truth was "the host
    declined to answer today". Same lesson, applied to the probes that remain:
    reachability of some other host is not evidence about this one.
    """
    global fetch
    saved = fetch
    deps = {"github": "https://github.com/RevolutionLA/adversarial-review",
            "raw": YUE_README,
            "npm": NPM_FIRST_CHUNK}
    try:
        for dep, url in deps.items():
            for code, want in ((200, True), (403, False), (429, False)):
                fetch = lambda u, retries=2, _u=url, _c=code: (
                    (_c, b"") if u == _u else (200, b""))
                got = probe(dep)
                if got != want:
                    return ("probe(%r) said %r while %s answered %d and every other URL "
                            "answered 200 - a green probe that cannot see this failure is "
                            "worse than no probe" % (dep, got, url, code))
    finally:
        fetch = saved
    return None


SITE_FILES = ("index.html", "assets/css/styles.css", "assets/js/main.js",
              "assets/img/og-image.png", "sitemap.xml")
def _copy_site(src, dst):
    for rel in SITE_FILES:
        d = os.path.join(dst, rel)
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy(os.path.join(src, rel), d)
    return dst


def _unit_throttle_is_not_a_dead_link():
    """Deterministic reverse case: 429 / 502 / 503 are the server reacting to OUR burst
    (this loop makes a dozen requests), not evidence about the page. Under a full-host
    throttle the checker must emit ZERO findings and a reason at every verdict site.

    Stubbed so it runs with no network at all, and it asserts the direction that a
    mutation case cannot: the checker must STAY SILENT about the URLs.
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="claims-throttle-")
    global fetch, _head_headers
    saved_fetch, saved_head = fetch, _head_headers
    try:
        _copy_site(ROOT, tmp)
        fetch = lambda url, retries=2: (429, b"")
        _head_headers = lambda url, timeout=25: (429, {"retry-after": "60"})
        found, unknown = check(tmp, offline=False)
    finally:
        fetch, _head_headers = saved_fetch, saved_head
        shutil.rmtree(tmp, ignore_errors=True)
    # Assert on the WHOLE verdict set, not on the wording of the one branch I happened
    # to think of. Round-4 review: this case checked `found` for the substring
    # "cannot reach", which the README branch never emits - a real false finding slipped
    # through and the case stayed green. A throttle must not produce ANY finding.
    if found:
        return "a 429 produced %d finding(s), expected 0: %r" % (len(found), found[0])
    # And every network verdict site must say so out loud: a branch that swallows the
    # throttle would look identical to one that handled it.
    missing = [site for site, marker in THROTTLE_SITES
               if not [u for u in unknown if all(m in u for m in marker)]]
    if missing:
        return "no inconclusive accounted for the throttle at: %s (the branch is silent " \
               "or unclassified): %r" % (", ".join(missing), unknown[:4])
    return None


UNIT_CASES = [
    ("transport classification table", _unit_transport_table),
    ("curl 000 is transport, not HTTP 0", _unit_curl_000),
    ("star parser blindness is not 'ok'", _unit_star_parser_blindness),
    ("curl HEAD reports the status it saw, not 200", _unit_head_status_is_observed),
    ("GitHub block page is not scraper blindness", _unit_blocked_page_is_not_blindness),
    ("HTTPError keeps the body that explains the verdict", _unit_httperror_body_survives),
    ("throttling must yield 0 findings and a reason at every verdict site",
     _unit_throttle_is_not_a_dead_link),
    ("probe asks the endpoint the verdict comes from",
     _unit_probe_asks_the_endpoint_that_matters),
]


def selftest(root):
    """Mutate a copy; the checker must notice each kind of breakage."""
    import tempfile
    rc = 0
    skipped = []
    probes = {}          # dep -> reachable, answered once per run (see CASES loop)
    for label, fn in UNIT_CASES:
        err = fn()
        if err and err.startswith(SKIP):
            note = err[len(SKIP):].strip()
            skipped.append("%s (%s)" % (label, note))
            print("selftest [%s] SKIPPED - %s" % (label, note))
        elif err:
            print("selftest FAILED - %s: %s" % (label, err))
            rc = 1
        else:
            print("selftest OK - %s" % label)
    for label, target, old, new, expect, dep, mode in CASES:
        if dep:
            # Memoised: several cases ask about the same host, and a throttled host
            # answers identically to all of them. Asking three ways is thorough; paying
            # three round trips is just a slower way to learn it declined today.
            if dep not in probes:
                probes[dep] = probe(dep)
            if not probes[dep]:
                skipped.append("%s (%s host unreachable)" % (label, dep))
                print("selftest [%s] SKIPPED - depends on unreachable host" % label)
                continue
        tmp = tempfile.mkdtemp(prefix="claims-selftest-")
        try:
            _copy_site(root, tmp)
            p = os.path.join(tmp, target)
            s = open(p, encoding="utf-8").read()
            if old not in s:
                print("selftest [%s] BROKEN - anchor text absent, case is stale" % label)
                rc = 1
                continue
            open(p, "w", encoding="utf-8").write(s.replace(old, new))
            found, unknown = check(tmp, offline=dep is None)
            if mode == "transport":
                accused = [f for f in found if "cannot reach" in f and expect in f]
                hedged = [u for u in unknown if expect in u]
                if accused:
                    print("selftest FAILED - %s accused a URL it could not reach: %r"
                          % (label, accused[0]))
                    rc = 1
                elif not hedged:
                    print("selftest FAILED - %s produced no inconclusive for %s: %r"
                          % (label, expect, unknown))
                    rc = 1
                else:
                    print("selftest OK - %s (refused to guess)" % label)
            elif any(expect in f for f in found):
                print("selftest OK - caught %s" % label)
            else:
                # If the host throttled this run, the case never got to observe
                # anything - saying "blind to X" would send the reader to fix a
                # checker that works. Misattribution is the failure this whole
                # file exists to prevent, so it applies to its own output too.
                # Markers name the verdict site that declined, not any old "unchecked":
                # a loose substring would let a genuinely blind checker skip itself green.
                throttled = [u for u in unknown
                             if any(mark in u for mark in
                                    # Each marker names a verdict site that declined to
                                    # answer, so a genuinely blind checker cannot skip
                                    # itself green with an unrelated "unchecked".
                                    ("throttled by the host", "block/abuse page"))]
                if throttled:
                    skipped.append("%s (host throttled the run)" % label)
                    print("selftest [%s] SKIPPED - host throttled the run: %s"
                          % (label, throttled[0]))
                else:
                    print("selftest FAILED - blind to %s: %r" % (label, found))
                    rc = 1
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    if skipped:
        print("(%d case(s) skipped: %s)" % (len(skipped), "; ".join(skipped)))
    total = len(CASES) + len(UNIT_CASES)
    print("%d case(s) ran, %d skipped (of %d)"
          % (total - len(skipped), len(skipped), total))
    if skipped and rc == 0:
        # An un-run case is not a passed case: exit 2 says "selftest was incomplete"
        # so CI cannot read a silent SKIP as a green board (review P1-B).
        print("selftest INCONCLUSIVE - coverage incomplete, do not treat as a pass")
        rc = 2
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
    if a.offline:
        print("note: --offline, so link / star / README claims were not checked")
    print("%d finding(s), %d inconclusive" % (len(found), len(unknown)))
    if found:
        sys.exit(1)
    sys.exit(2 if unknown else 0)


if __name__ == "__main__":
    main()
