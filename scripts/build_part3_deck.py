#!/usr/bin/env python3
"""Build the Part 3 LinkedIn announcement deck as whole-slide PNGs + a PDF.

This is the render-whole-slides-with-PIL approach mandated by
PROCESS/canva_deck_styleguide.md §8 (after the Canva-API restyle failed). We own
the entire 1920x1080 canvas, so there is no cover-crop clipping, no invisible
template shapes, and no font-family restriction. Every slide is composed from a
small set of primitives and one of four consistent templates (COVER / SPLIT /
PROMPT / GRID).

Run:  uv run python scripts/build_part3_deck.py
Out:  dist/part3-deck/slide-NN.png  +  "LinkedIn/Part 3 announcement deck.pdf"
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------
W, H = 1920, 1080
LM, RM = 110, 110          # side margins
TM = 84                    # title top
CW = W - LM - RM           # content width (1700)
RIGHT = W - RM             # right content edge (1810)

# ---------------------------------------------------------------------------
# Palette (editorial; navy + teal sampled from the approved deck)
# ---------------------------------------------------------------------------
NAVY   = (0, 31, 63)       # #001F3F  slide background
TEAL   = (160, 207, 220)   # #A0CFDC  titles / accents / labels
BODY   = (214, 222, 234)   # light body text
MUTED  = (150, 165, 190)   # secondary text / explanations
HAIR   = (74, 110, 132)    # hairline rule (muted teal)
PANEL  = (255, 255, 255, 18)   # faint prompt-panel fill
MONOFG = (232, 238, 246)   # monospace query text
FRAME  = (60, 86, 110)     # screenshot border

# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------
_SERIF    = ["/System/Library/Fonts/NewYork.ttf", "/System/Library/Fonts/Supplemental/Georgia.ttf"]
_SERIF_IT = ["/System/Library/Fonts/NewYorkItalic.ttf", "/System/Library/Fonts/Supplemental/Georgia Italic.ttf"]
_SANS     = ["/System/Library/Fonts/SFNS.ttf"]
_MONO     = ["/System/Library/Fonts/SFNSMono.ttf"]

_font_cache: dict = {}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    key = (kind, size)
    if key in _font_cache:
        return _font_cache[key]
    paths = {"serif": _SERIF, "serif_it": _SERIF_IT, "sans": _SANS, "mono": _MONO}[kind]
    for p in paths:
        try:
            f = ImageFont.truetype(p, size)
            _font_cache[key] = f
            return f
        except OSError:
            continue
    raise RuntimeError(f"no font found for {kind}")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------
def wrap(d: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, maxw: int) -> list[str]:
    out: list[str] = []
    for para in text.split("\n"):
        if para == "":
            out.append("")
            continue
        cur = ""
        for word in para.split(" "):
            t = (cur + " " + word).strip()
            if d.textlength(t, font=f) <= maxw:
                cur = t
            else:
                if cur:
                    out.append(cur)
                cur = word
        out.append(cur)
    return out


def draw_lines(d, lines, x, y, f, fill, lh, bold=False) -> int:
    """Draw wrapped lines top-down; return the y just below the last line."""
    sw = 0.6 if bold else 0
    for ln in lines:
        if ln:
            d.text((x, y), ln, font=f, fill=fill, stroke_width=sw, stroke_fill=fill)
        y += lh
    return y


def tracked(d, pos, text, f, fill, tracking):
    x, y = pos
    for ch in text:
        d.text((x, y), ch, font=f, fill=fill)
        x += d.textlength(ch, font=f) + tracking


# ---------------------------------------------------------------------------
# Shared chrome
# ---------------------------------------------------------------------------
def base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), NAVY)
    return img, ImageDraw.Draw(img)


def title(d, text, size=92, maxw=1500) -> int:
    f = font("serif", size)
    lines = wrap(d, text, f, maxw)
    y = draw_lines(d, lines, LM, TM, f, TEAL, int(size * 1.14))
    return y


def hairline(d, y) -> int:
    yy = y + 22
    d.line([(LM, yy), (RIGHT, yy)], fill=HAIR, width=2)
    return yy + 2


def subtitle(d, text, y, size=50, maxw=CW) -> int:
    f = font("serif_it", size)
    lines = wrap(d, text, f, maxw)
    return draw_lines(d, lines, LM, y, f, TEAL, int(size * 1.2))


def body(d, text, x, y, w, size=34, fill=BODY) -> int:
    f = font("sans", size)
    lines = wrap(d, text, f, w)
    # blank lines (paragraph breaks) get a half-line of space
    lh = int(size * 1.42)
    for ln in lines:
        if ln:
            d.text((x, y), ln, font=f, fill=fill)
            y += lh
        else:
            y += int(lh * 0.55)
    return y


def point(d, label, desc, x, y, w):
    """A grid/list cell: teal label + light description. Returns bottom y."""
    lf = font("sans", 40)
    d.text((x, y), label, font=lf, fill=TEAL, stroke_width=0.6, stroke_fill=TEAL)
    y += 58
    return body(d, desc, x, y, w, size=29, fill=BODY)


def prompt_block(img, d, query, explanation, region_top, region_bottom=H - 110):
    """Monospace prompt panel + sans explanation, vertically centered in the
    region between region_top and region_bottom."""
    px0, px1 = 150, W - 150
    pad = 54
    qf = font("mono", 42)
    inner = (px1 - px0) - 2 * pad
    qlines = wrap(d, query, qf, inner)
    qlh = 62
    box_h = len(qlines) * qlh + 2 * pad
    ef = font("sans", 33)
    elh = int(33 * 1.42)
    elines = wrap(d, explanation, ef, px1 - px0 - 4)
    gap = 44
    total = box_h + gap + len(elines) * elh
    y = region_top + max(0, (region_bottom - region_top - total) // 2)
    # faint panel (RGBA over the RGB canvas)
    overlay = Image.new("RGBA", (px1 - px0, box_h), (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    od.rounded_rectangle([0, 0, px1 - px0 - 1, box_h - 1], radius=18, fill=PANEL)
    od.rectangle([0, 0, 9, box_h], fill=TEAL)          # teal left bar
    img.paste(overlay, (px0, y), overlay)
    ty = y + pad
    for ln in qlines:
        d.text((px0 + pad, ty), ln, font=qf, fill=MONOFG)
        ty += qlh
    draw_lines(d, elines, px0 + 2, y + box_h + gap, ef, MUTED, elh)


def paste_image(img, d, path, box, label=None):
    """CONTAIN-fit a screenshot into box=(x,y,w,h): never cropped, centered,
    rounded corners + thin border. Optional teal label above the box."""
    x, y, w, h = box
    if label:
        lf = font("sans", 30)
        d.text((x, y - 44), label, font=lf, fill=TEAL, stroke_width=0.5, stroke_fill=TEAL)
    im = Image.open(path).convert("RGBA")
    iw, ih = im.size
    scale = min(w / iw, h / ih)
    nw, nh = max(1, int(iw * scale)), max(1, int(ih * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    px, py = x + (w - nw) // 2, y + (h - nh) // 2
    mask = Image.new("L", (nw, nh), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, nw - 1, nh - 1], radius=16, fill=255)
    img.paste(im, (px, py), mask)
    d.rounded_rectangle([px, py, px + nw - 1, py + nh - 1], radius=16, outline=FRAME, width=2)
    return (px, py, nw, nh)


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------
def slide01():
    img, d = base()
    tracked(d, (LM, 70), "CLAUDE EXPLORER", font("sans", 30), TEAL, 6)
    d.line([(LM, 116), (RIGHT, 116)], fill=HAIR, width=2)
    tf = font("serif", 112)
    d.text((LM, 150), "The MCP Server", font=tf, fill=TEAL)
    sf = font("serif_it", 56)
    d.text((LM, 300), "Let Claude Analyze Your Claude Conversations", font=sf, fill=TEAL)
    d.text((LM, 392), "Raymond Peck", font=font("sans", 34), fill=BODY)
    paste_image(img, d, asset("ouroboros.png"), (360, 470, 1200, 560))
    return img


def slide02():
    img, d = base()
    yb = hairline(d, title(d, "From Archive to Answers"))
    yb = subtitle(d, "Part 2 is for humans. Part 3 is for your AI.", yb + 34)
    body(d,
         "Part 2 saved your whole Claude history to your disk and let you "
         "manually browse and search.\n\n"
         "Part 3 lets a brand-new Claude chat query it over MCP, no copy-paste, "
         "across your Code, Desktop, and Cowork sessions.",
         LM, yb + 30, 760)
    paste_image(img, d, asset("part3-deck-tool-use.png"), (980, 360, 830, 470))
    return img


def slide03():
    img, d = base()
    yb = hairline(d, title(d, "Ask Claude to Analyze Claude"))
    prompt_block(img, d,
                 "In my longest Claude Code conversation, what did we decide "
                 "about the database, and what's still open?",
                 "Claude finds the conversation, skims it, reads only the parts "
                 "that matter, and answers. You ask in plain English; no commands "
                 "to learn.",
                 yb)
    return img


def slide04():
    img, d = base()
    yb = hairline(d, title(d, "How It Works"))
    y = yb + 50
    y = point(d, "Five read-only tools",
              "Find, outline, read, and export, all read-only.", LM, y, 760)
    point(d, "One question",
          "You ask one high-level question, and Claude composes the steps for "
          "you to return the answer.", LM, y + 46, 760)
    paste_image(img, d, asset("part3-deck-mcp-tools.png"), (980, 360, 830, 470))
    return img


def slide05():
    img, d = base()
    yb = hairline(d, title(d, "The Outline-First Pattern"))
    yb = subtitle(d, "Skim the outline, read only what matters, keep it fast "
                     "and cheap.", yb + 30, size=44)
    cells = [
        ("Skim first",
         "Claude pulls a one-line-per-message outline before reading anything "
         "in full."),
        ("Read what matters",
         "From that outline it reads only the handful of messages that answer "
         "your question."),
        ("Huge sessions",
         "A single Claude Code session can run to thousands of messages and "
         "still answer fast."),
        ("Fast and cheap",
         "You pour in only the parts that matter, so a giant archive stays "
         "quick and inexpensive."),
    ]
    grid(d, cells, yb + 44)
    return img


def slide06():
    img, d = base()
    yb = hairline(d, title(d, "Summarize a Sprawling Conversation"))
    prompt_block(img, d,
                 "What did we decide in my longest conversation, and what's "
                 "still unresolved?",
                 "Claude skims the outline, reads the parts that matter, and "
                 "hands you the decisions, without re-reading the whole thing.",
                 yb)
    return img


def slide07():
    img, d = base()
    yb = hairline(d, title(d, "The Self-Tuning Loop"))
    prompt_block(img, d,
                 "Find the mistakes Claude keeps making, and write rules for "
                 "my CLAUDE.md.",
                 "Mine your own history for recurring errors, then sharpen your "
                 "CLAUDE.md and agent prompts. Your future sessions get a little "
                 "smarter, and this compounds over time as you use it.",
                 yb)
    return img


def slide08():
    img, d = base()
    yb = hairline(d, title(d, "It Researched and Drafted This Series"))
    body(d,
         "I pointed the MCP server at this project's own 5,000-message build "
         "history, and it mined the decisions and memorable moments into a "
         "drafting brief for this series.\n\n"
         "The original prompt had already named both workflows, before either "
         "existed.",
         LM, yb + 50, 720)
    paste_image(img, d, asset("part3-deck-slide8-prompt.png"), (1010, 300, 800, 700))
    return img


def slide09():
    img, d = base()
    yb = hairline(d, title(d, "Connect It in One Command"))
    top = yb + 90
    paste_image(img, d, asset("part3-deck-terminal.png"),
                (LM, top, 830, 470), label="Claude Code")
    paste_image(img, d, asset("part3-deck-slide9-json.png"),
                (980, top, 830, 470), label="Claude Desktop")
    d.text((LM, top + 510), "Same steps on macOS, Windows, and Linux.",
           font=font("sans", 30), fill=MUTED)
    return img


def slide10():
    img, d = base()
    yb = hairline(d, title(d, "Safe by Design"))
    yb = subtitle(d, "Read-only, local, and restrained by design.", yb + 30, size=44)
    cells = [
        ("Read-only", "It can look, but it can't change or delete anything."),
        ("Local", "Nothing leaves your machine, and there's no server to phone "
                  "home to."),
        ("Restrained", "Claude uses it only when you ask."),
    ]
    grid(d, cells, yb + 44)
    return img


def slide11():
    img, d = base()
    yb = hairline(d, title(d, "Read Part 3 on Medium"))
    cells = [
        ("Quickstart", "Connect it in five minutes."),
        ("User Guide", "The full, practical walkthrough of every feature."),
        ("Deep Dive", "The real numbers and the design decisions behind the server."),
        ("Read more at", "medium.com/@raymondpeck"),
    ]
    grid(d, cells, yb + 60)
    return img


def slide12():
    img, d = base()
    yb = hairline(d, title(d, "Jump In"))
    cells = [
        ("Install", "uvx claude-explorer mcp"),
        ("GitHub", "github.com/rpeck/claude-explorer"),
        ("PyPI", "pypi.org/project/claude-explorer"),
    ]
    grid(d, cells, yb + 60, mono_desc=True)
    return img


# ---------------------------------------------------------------------------
# Grid (used by slides 5, 10, 11, 12) — 2 columns, consistent x/y
# ---------------------------------------------------------------------------
COL_X = [LM, 990]
COL_W = 760
ROW_GAP = 300


def grid(d, cells, top, mono_desc=False):
    for i, (label, desc) in enumerate(cells):
        col, row = i % 2, i // 2
        x = COL_X[col]
        y = top + row * ROW_GAP
        lf = font("sans", 40)
        d.text((x, y), label, font=lf, fill=TEAL, stroke_width=0.6, stroke_fill=TEAL)
        y += 60
        if mono_desc:
            d.text((x, y), desc, font=font("mono", 30), fill=BODY)
        else:
            body(d, desc, x, y, COL_W, size=29, fill=BODY)


# ---------------------------------------------------------------------------
# Assets / output
# ---------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def asset(name: str) -> str:
    return os.path.join(ROOT, "articles", "Attachments", name)


def main():
    slides = [slide01, slide02, slide03, slide04, slide05, slide06,
              slide07, slide08, slide09, slide10, slide11, slide12]
    outdir = os.path.join(ROOT, "dist", "part3-deck")
    os.makedirs(outdir, exist_ok=True)
    os.makedirs(os.path.join(ROOT, "LinkedIn"), exist_ok=True)
    imgs = []
    for i, fn in enumerate(slides, 1):
        im = fn()
        p = os.path.join(outdir, f"slide-{i:02d}.png")
        im.save(p)
        imgs.append(im.convert("RGB"))
        print("wrote", p)
    pdf = os.path.join(ROOT, "LinkedIn", "Part 3 announcement deck.pdf")
    imgs[0].save(pdf, "PDF", resolution=96.0, save_all=True, append_images=imgs[1:])
    print("wrote", pdf)


if __name__ == "__main__":
    main()
