"""Colour themes (static/themes.css): WCAG contrast for every theme x mode, the cascade rules the file promises,
and no colour literal outside the token files.

The cascade is emulated the way the browser does it for the selectors themes.css uses: `:root`, `[data-mode=..]`,
`[data-theme=..]`, `[data-theme]` and `:not([data-theme=..])` on :root, ordered by specificity then source order.
`var()` and `color-mix(in srgb, A p%, B)` are resolved, so mixed tints are checked like literal ones.
"""

import math
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "static"
CSS = (STATIC / "themes.css").read_text(encoding="utf-8")
THEMES = ["mono", "ocean", "forest", "sunset", "contrast"]
MODES = ["light", "dark"]


# ---- parsing + cascade ------------------------------------------------------------------------------------
def blocks():
    text = re.sub(r"/\*.*?\*/", "", CSS, flags=re.S)
    out = []
    for i, m in enumerate(re.finditer(r"([^{}]+)\{([^{}]*)\}", text)):
        decls = dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", m.group(2) + ";"))
        for sel in m.group(1).split(","):
            out.append((sel.strip(), decls, i))
    return out


def match(sel, theme, mode):
    """(specificity, matches) for a :root selector, or None for anything else (.sw, html.theme-anim, @media)."""
    if not sel.startswith(":root") or " " in sel:
        return None
    rest, spec = sel[len(":root"):], 1
    attrs = {"data-theme": theme, "data-mode": mode}
    for neg, name, val in re.findall(r"(:not\()?\[([\w-]+)(?:=\"([\w-]+)\")?\]\)?", rest):
        spec += 1
        ok = name in attrs and (not val or attrs[name] == val)
        if neg:
            ok = not ok
        if not ok:
            return spec, False
    return spec, True


def cascade(theme, mode):
    hits = []
    for sel, decls, order in blocks():
        r = match(sel, theme, mode)
        if r and r[1]:
            hits.append((r[0], order, decls))
    tokens = {}
    for _, _, decls in sorted(hits, key=lambda h: (h[0], h[1])):
        tokens.update(decls)
    return tokens


def hexrgb(h):
    h = h.lstrip("#")
    h = "".join(c * 2 for c in h) if len(h) == 3 else h
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def resolve(tokens, value, depth=0):
    """A token value -> (r, g, b), or None for values that aren't one opaque colour (transparent, shadows)."""
    assert depth < 20, f"var() loop at {value}"
    value = value.strip()
    value = re.sub(r"var\((--[\w-]+)\)", lambda m: tokens[m.group(1)].strip(), value)
    if re.fullmatch(r"#[0-9a-fA-F]{3,8}", value):
        return hexrgb(value)
    m = re.fullmatch(r"color-mix\(in srgb,\s*(.+?)\s+([\d.]+)%,\s*(.+)\)", value)
    if m:
        a, p, b = resolve(tokens, m.group(1), depth + 1), float(m.group(2)) / 100, resolve(tokens, m.group(3), depth + 1)
        if a is None or b is None:
            return None
        return tuple(round(x * p + y * (1 - p)) for x, y in zip(a, b))
    return None


def colours(theme, mode):
    t = cascade(theme, mode)
    return {k: c for k in t if (c := resolve(t, t[k])) is not None}


def lum(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def lab(rgb):
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in rgb)
    x, y, z = (r * .4124 + g * .3576 + b * .1805) / .95047, r * .2126 + g * .7152 + b * .0722, (r * .0193 + g * .1192 + b * .9505) / 1.08883
    f = lambda t: t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    return 116 * f(y) - 16, 500 * (f(x) - f(y)), 200 * (f(y) - f(z))


def delta_e(a, b):
    return math.dist(lab(a), lab(b))


CASES = [(t, m) for t in THEMES for m in MODES]


# ---- contrast ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("theme,mode", CASES)
def test_text_contrast(theme, mode):
    c = colours(theme, mode)
    body = 7.0 if theme == "contrast" else 4.5                      # High contrast: AAA
    bad = []

    def need(fg, bg, k):
        r = contrast(c[fg], c[bg])
        if r < k:
            bad.append(f"{fg} on {bg}: {r:.2f} < {k}")

    for fg in ("--text", "--text-2", "--text-3"):
        for bg in ("--bg", "--bg-2", "--bg-3"):
            need(fg, bg, body)
    for hue in ("red", "amber", "blue", "green"):
        for bg in ("--bg", "--bg-2", f"--tint-{hue}"):
            need(f"--{hue}-ink", bg, body)                         # small coloured text, chips, badges
    need("--on-accent", "--accent", 4.5)                           # primary buttons, pressed toggles, active chips
    need("--on-accent", "--accent-hover", 4.5)
    need("--kbd-on-accent-text", "--accent", 3.0)                  # the key hint inside a primary button (UI)
    for fg in ("--inv-strong", "--inv-text", "--inv-text-2"):
        need(fg, "--inv-bg", 4.5)                                  # the judge tour card
    need("--orb-ink", "--stage-b", 4.5)                            # FRONT / BACK / LEFT / RIGHT on the floor
    need("--orb-ink", "--stage-c", 4.5)
    assert not bad, f"{theme}/{mode}: " + "; ".join(bad)


@pytest.mark.parametrize("theme,mode", CASES)
def test_semantic_marks_stand_out_and_stay_distinct(theme, mode):
    c = colours(theme, mode)
    bad = []
    marks = {"speech": "--blue", "unknown": "--violet", "alert": "--red", "live": "--green", "sound": "--amber"}
    for kind, t in marks.items():
        # Non-text contrast (WCAG 1.4.11) 3:1. Amber on a light page is the one exception, kept from the original
        # design: an amber mark always has its own shape (diamond) and a text label, and amber TEXT uses --amber-ink
        # (checked above). High contrast has no exception.
        need = 1.9 if (t == "--amber" and mode == "light" and theme != "contrast") else 3.0
        for bg in ("--bg", "--stage-c"):
            r = contrast(c[t], c[bg])
            if r < need:
                bad.append(f"{kind} {t} on {bg}: {r:.2f} < {need}")
    names = ["speech", "sound", "unknown", "alert"]
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            d = delta_e(c[marks[a]], c[marks[b]])
            if d < 20:
                bad.append(f"{a} vs {b}: delta E {d:.1f} < 20")
    assert contrast(c["--orb-point"], c["--stage-c"]) >= 3.0, f"{theme}/{mode}: the point field fades into the stage"
    assert not bad, f"{theme}/{mode}: " + "; ".join(bad)


def test_mono_light_is_the_original_look():
    c = colours("mono", "light")
    assert c["--bg"] == hexrgb("#ffffff") and c["--text"] == hexrgb("#171717") and c["--blue"] == hexrgb("#0070f3")
    assert c["--accent"] == c["--text"] and c["--tint-amber"] == hexrgb("#fff7e6")


# ---- the file's own rules ---------------------------------------------------------------------------------
def declared(selector):
    return {k for sel, decls, _ in blocks() if sel == selector for k in decls}


@pytest.mark.parametrize("theme", [t for t in THEMES if t != "mono"])
def test_a_dark_theme_block_overrides_everything_its_light_block_sets(theme):
    light = declared(f':root[data-theme="{theme}"]')
    dark = declared(f':root[data-theme="{theme}"][data-mode="dark"]')
    assert light, f"no light block for {theme}"
    missing = sorted(light - dark)
    assert not missing, f"{theme} dark leaks its light values for {missing}"


def test_every_theme_has_a_swatch_in_both_modes_and_the_page_knows_every_theme():
    for t in THEMES:
        assert f'.sw[data-sw="{t}"]' in CSS and f':root[data-mode="dark"] .sw[data-sw="{t}"]' in CSS, t
    js = (STATIC / "theme.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for t in THEMES:
        assert f"id: '{t}'" in js and f"'{t}'" in html, f"{t} missing from theme.js or the pre-paint script"


def test_no_colour_literal_outside_the_token_files():
    """Every colour is a token (themes.css); the one other palette is the print palette of the static
    architecture SVG (architecture.js PRINT, used where there is no DOM to read tokens from)."""
    literal = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d|hsla?\(\s*\d|0x[0-9a-fA-F]{6}")
    bad = []
    for f in sorted(STATIC.glob("*.js")) + sorted(STATIC.glob("*.css")) + [STATIC / "index.html"]:
        if f.name == "themes.css":
            continue
        text = f.read_text(encoding="utf-8")
        if f.name == "architecture.js":
            text = re.sub(r"export const PRINT = \{.*?\}\n", "", text, flags=re.S)
        for n, line in enumerate(text.splitlines(), 1):
            if literal.search(line):
                bad.append(f"{f.name}:{n}: {line.strip()[:80]}")
    assert not bad, "colour literals outside themes.css:\n" + "\n".join(bad)


def test_the_switcher_never_mistakes_html_for_a_choice():
    """<html> carries data-theme / data-mode, so closest('[data-theme]') from any click inside the switcher finds it:
    picking a theme then silently turned System into Light or Dark. The switcher's buttons use data-pick-*."""
    js = (STATIC / "theme.js").read_text(encoding="utf-8")
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "closest('[data-mode]')" not in js and "closest('[data-theme]')" not in js
    assert html.count("data-pick-mode=") == 3 and "data-pick-theme=" in js
