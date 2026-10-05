"""Regenerate assets/img/og-image.png (1200x630 social preview).

    python tools/gen-og-image.py

Needs Pillow plus one CJK sans with a Bold and a Medium cut, and a monospace.
The card was first drawn with HarmonyOS Sans SC + Consolas on Windows; the same
layout is reproduced on Linux with Noto Sans CJK SC + Liberation Mono (which is
metric-compatible with Consolas, so the mono rows land where they were designed
to). Each face is resolved from a candidate list, so the tool runs on either box
instead of only the one it was first written on.
"""
import math
import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _face(candidates, want_sc=True):
    """First existing font file wins; for a .ttc, find the face index that is the
    Simplified-Chinese one. Hardcoding index=2 would quietly switch the card to
    Japanese glyph variants the day the package reorders its faces."""
    for path in candidates:
        if not os.path.exists(path):
            continue
        if not want_sc:
            return path, 0
        for i in range(12):
            try:
                f = ImageFont.truetype(path, 12, index=i)
            except OSError:
                break
            if "SC" in f.getname()[0]:
                return path, i
        return path, 0          # 单面字体：没有 SC 变体可挑，就用第一个
    return None, 0


ZH_B = _face(["C:/Windows/Fonts/HarmonyOS_Sans_SC_Bold.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"])
ZH_M = _face(["C:/Windows/Fonts/HarmonyOS_Sans_SC_Medium.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc"])
MONO_B = _face(["C:/Windows/Fonts/consolab.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf"],
               want_sc=False)
MONO_R = _face(["C:/Windows/Fonts/consola.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf"],
               want_sc=False)
_missing = [n for n, spec in (("中文 Bold", ZH_B), ("中文 Medium", ZH_M),
                              ("等宽 Bold", MONO_B), ("等宽 Regular", MONO_R))
            if spec[0] is None]
if _missing:
    sys.exit("gen-og-image: no font found for %s - point _face() at a local file "
             "instead of shipping a card drawn with a fallback font"
             % ", ".join(_missing))

W, H = 1200, 630
SPACE0 = (7, 9, 13)
INK = (223, 230, 238)
DIM = (139, 150, 166)
FAINT = (93, 104, 120)
SIGNAL = (52, 240, 160)
AMBER = (242, 178, 76)

base = Image.new("RGB", (W, H), SPACE0)
glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
g = ImageDraw.Draw(glow)

# 右上角信号绿辉光
for r in range(560, 120, -10):
    a = int(7 + (560 - r) / 44)
    g.ellipse([W - 170 - r, -230 - r // 2, W - 170 + r, -230 + r // 2 + r],
              fill=(52, 240, 160, min(a, 26)))

base = Image.alpha_composite(base.convert("RGBA"), glow).convert("RGB")
d = ImageDraw.Draw(base, "RGBA")

# 网格
for x in range(0, W, 40):
    d.line([(x, 0), (x, H)], fill=(148, 168, 190, 18), width=1)
for y in range(0, H, 40):
    d.line([(0, y), (W, y)], fill=(148, 168, 190, 18), width=1)


def font(spec, size):
    path, index = spec
    return ImageFont.truetype(path, size, index=index)


# 示波器波形（右下角，站点 hero 同款折线语汇）
pts = []
for px in range(0, W + 1, 3):
    t = px / W
    y = 452 + math.sin(t * 22) * 9 + math.sin(t * 5.1) * 5
    seg = int(t * 6) % 6
    if seg == 1:
        y += ((px % 60) - 30) * 1.15
    if seg == 3:
        y -= ((px % 46) - 23) * 0.9
    pts.append((px, y))

for wdt, alpha, col in [(9, 26, SIGNAL), (5, 60, SIGNAL), (2, 235, SIGNAL)]:
    d.line(pts, fill=(col[0], col[1], col[2], alpha), width=wdt, joint="curve")
d.rectangle([0, 500, W, H], fill=SPACE0)
d.line(pts, fill=(52, 240, 160, 90), width=1)

# 顶部 mono 标签
d.text((72, 52), ">_", font=font(MONO_B, 26), fill=SIGNAL)
d.text((112, 56), "RevolutionLA", font=font(MONO_B, 21), fill=INK)
d.text((72, 96), "SIGNAL INBOUND · AI EXPLORER", font=font(MONO_R, 15), fill=FAINT)
d.line([(690, 68), (W - 72, 68)], fill=(148, 168, 190, 46), width=1)
# 不带项目计数：数字会随主页增删而失真
d.text((W - 72, 56), "WORK · OPEN SOURCE", font=font(MONO_R, 15), fill=DIM, anchor="ra")

# 主标题
d.text((68, 168), "刘昂", font=font(ZH_B, 116), fill=INK)
d.text((352, 226), "/", font=font(MONO_R, 78), fill=FAINT)
d.text((392, 226), "RevolutionLA", font=font(MONO_B, 62), fill=(223, 230, 238, 235))

# 座右铭
y = 340
d.text((72, y), "我看到的不是技术，而是", font=font(ZH_M, 34), fill=DIM)
w1 = d.textlength("我看到的不是技术，而是", font=font(ZH_M, 34))
d.text((72 + w1, y), "可能性", font=font(ZH_B, 34), fill=AMBER)
w2 = d.textlength("可能性", font=font(ZH_B, 34))
d.text((72 + w1 + w2, y), "。", font=font(ZH_M, 34), fill=DIM)

# 底部信息条
d.rectangle([0, H - 74, W, H], fill=(12, 18, 26, 235))
d.line([(0, H - 74), (W, H - 74)], fill=(52, 240, 160, 70), width=1)
d.text((72, H - 46), "LOC · CHENGDU  31.23°N 104.07°E", font=font(MONO_R, 17), fill=FAINT)

# 右侧含中文，Consolas 无中文字形 -> 中文用 HarmonyOS，URL 用 Consolas
f_zh, f_mono = font(ZH_M, 18), font(MONO_R, 17)
phrase, url = "LLM 部署 × 昇腾生态 × 开源插件", "revolutionla.github.io"
gap = 16
wa = d.textlength(phrase, font=f_zh)
wb = d.textlength(url, font=f_mono)
x0 = W - 72 - wb - gap - wa
d.text((x0, H - 47), phrase, font=f_zh, fill=DIM)
d.text((x0 + wa + gap, H - 46), url, font=f_mono, fill=SIGNAL)

out = os.path.join(ROOT, "assets", "img", "og-image.png")
base.save(out, "PNG", optimize=True)
print(out, os.path.getsize(out))
