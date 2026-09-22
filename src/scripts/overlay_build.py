"""Pure overlay-layout compiler for the Control Center's visual overlay builder
(#114). Turns a layout model, per-slot position and style overrides, uploaded
fonts and a verbatim customCss escape hatch, into the override CSS the relay
already serves at /<page>/override.css. No I/O and no dependencies. Tests:
tests/test_overlay.py. Spec:
docs/superpowers/specs/2026-06-13-visual-overlay-builder-design.md.

The editable slots are NOT hardcoded here: extract_slots() reads the data-edit
markers from the base page (src/obs/hud.html), so the markup is the single source
of truth and a new marked element becomes editable automatically.
"""
import re

# Font name and type whitelist, DUPLICATED from src/relay/racecast-feeds.py and
# pinned byte-identical by a cross-check in tests/test_overlay.py. Keep them in
# sync.
FONT_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
FONT_CTYPES = {"woff2": "font/woff2", "woff": "font/woff",
               "ttf": "font/ttf", "otf": "font/otf"}
FONT_EXTS = tuple(FONT_CTYPES)

# Property key -> CSS property name. The single canonical map; the builder's live
# canvas applies the same standard properties inline, so nothing drifts.
_PX_PROPS = {
    "left": "left", "top": "top", "width": "width", "height": "height",
    "fontSize": "font-size", "borderWidth": "border-width",
    "teamNameMax": "--team-name-max", "teamNameMin": "--team-name-min",
    "padding": "padding", "borderRadius": "border-radius",
    "letterSpacing": "letter-spacing",
}
_TEXT_PROPS = {"color": "color", "background": "background",
               "borderColor": "border-color", "borderStyle": "border-style"}
_ALIGN = {"left": "flex-start", "center": "center", "right": "flex-end"}
_VALIGN = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}
_TEXT_TRANSFORM = {"none": "none", "uppercase": "uppercase",
                   "lowercase": "lowercase", "capitalize": "capitalize"}
_FONT_WEIGHT = {"normal": "normal", "bold": "bold"}
_FONT_STYLE = {"normal": "normal", "italic": "italic"}

# The default property set offered for a text slot with no data-edit-props attr.
DEFAULT_PROPS = ("left", "top", "width", "height", "fontSize",
                 "fontFamily", "color", "background", "align")

# Stable emit order within a slot rule, independent of dict insertion order.
PROP_ORDER = ("left", "top", "width", "height", "padding",
              "fontSize", "lineHeight", "letterSpacing",
              # slant (clip-path) emits after the border props and before the
              # text-sizing vars; shear rides with rotation in one transform.
              "borderWidth", "borderRadius", "slant",
              "teamNameMax", "teamNameMin", "fontFamily", "fontWeight",
              "fontStyle", "color", "background", "borderColor", "borderStyle",
              "align", "valign", "textTransform", "opacity",
              "rotation", "shear", "textShadow", "visible")

# Slot kinds: the single source for which properties a slot offers. extract_slots
# derives slot["props"] from the element's data-edit-kind. `text` is a strict
# superset of `box`, which covers a container or image (position, size, fill,
# border, opacity, rotation); text adds the type properties. Spec:
# docs/superpowers/specs/2026-06-15-overlay-builder-standard-properties-design.md.
KIND_BOX = ("left", "top", "width", "height", "padding",
            "background", "borderWidth", "borderStyle", "borderColor",
            "borderRadius", "slant", "opacity", "rotation", "shear", "visible")
KIND_TEXT = KIND_BOX + ("fontSize", "lineHeight", "letterSpacing",
                        "fontFamily", "fontWeight", "fontStyle", "color",
                        "align", "valign", "textTransform", "textShadow")
KIND_PROPS = {"text": KIND_TEXT, "box": KIND_BOX}

# A structured value must never close the rule or inject extra CSS; the only
# verbatim path is customCss. Reject anything carrying CSS-structural characters.
_UNSAFE_VALUE = re.compile(r"[;{}<>]|/\*|\*/")

# Sample content for the same-origin builder canvas, so the operator positions
# slots against realistic text. Each team is four slots (#136): number, name and
# brand carry text, the logo is an image. An image slot, the round flag or a team
# logo, carries a {"flag"/"brand": key} entry so the offline canvas previews it
# from bundled src/assets/, served at /api/overlay/asset/{flags,brands}/<key>.
SAMPLE = {
    "hud": {
        "stint": "STINT 3", "session": "Race",
        "streamer": "twitch.tv/commentary",
        "round-top": "Round 4", "round-country": "Belgium",
        "team1-num": "7", "team1-name": "Team Redline", "team1-brand": "BMW",
        "team1-quali": "1:38.973",
        "team2-num": "23", "team2-name": "Apex Racing", "team2-brand": "Porsche",
        "team2-quali": "1:39.104",
        "team3-num": "99", "team3-name": "Night Shift Motorsport", "team3-brand": "Ferrari",
        "team3-quali": "1:40.512",
        "race-control": "FCY: Full Course Yellow",
        "clock": "1:23:45",
        "pov-name": "John Doe",
        "flag-status": "Safety Car",
        "tele-delta": "-0.42s",
        "tele-pred-lbl": "PRED. LAP",
        "tele-pred": "1:35.812",
        "tele-fuel-lbl": "FUEL",
        "tele-fuel": "42% · 12 laps",
        "tele-top-lbl": "TOP SPEED",
        "tele-top": "291 km/h",
        "tele-clock": "18:10:38",
        "tele-avg-lbl": "AVG LAP",
        "tele-avg": "1:36.205",
        "tele-dist-lbl": "DISTANCE",
        "tele-dist": "1234.5 km",
        "round-flag": {"flag": "belgium"},
        "team1-logo": {"brand": "bmw"},
        "team2-logo": {"brand": "porsche"},
        "team3-logo": {"brand": "ferrari"},
    },
}

# Flag states offered in the builder's session-only preview picker. Each entry is
# {state, label}: `state` is the #flag-status[data-state="..."] CSS hook in
# src/obs/hud.html, which the canvas sets to preview the colour, and `label` is the
# banner text. Every `state` MUST exist as a data-state rule in hud.html;
# tests/test_overlay.py guards the drift.
FLAG_PRESETS = (
    {"state": "green-flag", "label": "Green Flag"},
    {"state": "yellow-flag", "label": "Yellow Flag"},
    {"state": "double-yellow", "label": "Double Yellow"},
    {"state": "safety-car", "label": "Safety Car"},
    {"state": "virtual-safety-car", "label": "Virtual Safety Car"},
    {"state": "full-course-yellow", "label": "Full Course Yellow"},
    {"state": "code-60", "label": "Code 60"},
    {"state": "red-flag", "label": "Red Flag"},
    {"state": "checkered-flag", "label": "Checkered Flag"},
)

# Curated free Google Fonts offered in the builder. Self-hosted on pick: the
# .woff2 is downloaded once into overlay/fonts/, so the live overlay stays offline
# and the canvas can preview it. Condensed and display families lead the list.
GOOGLE_FONTS = (
    "Oswald", "Teko", "Rajdhani", "Saira", "Saira Condensed", "Barlow",
    "Barlow Condensed", "Anton", "Bebas Neue", "Montserrat", "Roboto",
    "Roboto Condensed", "Inter", "Poppins", "Orbitron", "Exo 2",
    "Titillium Web", "Archivo", "Archivo Narrow", "Chakra Petch",
    "Russo One", "Michroma",
)


def font_family(filename):
    """The @font-face family name for an uploaded font: its file stem."""
    return filename.rsplit(".", 1)[0]


def google_font_filename(name):
    """Local filename for a self-hosted Google font: spaces and punctuation
    stripped plus .woff2, since FONT_NAME_RE forbids spaces. The CSS family is
    font_family() of it, so 'Saira Condensed' -> 'SairaCondensed.woff2'."""
    return re.sub(r"[^A-Za-z0-9]", "", name) + ".woff2"


# A plausible Google font family name: letters, digits and single spaces, 1 to 50
# long, no leading or trailing space. This is the gate for fetching ANY Google font,
# since the curated GOOGLE_FONTS is only the UI quick-pick. With the fixed
# googleapis host and the gstatic-only woff2 check it keeps the fetch SSRF-safe.
GOOGLE_FONT_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9 ]{0,48}[A-Za-z0-9])?$")


def is_google_font_name(name):
    """True for a syntactically valid Google font family name, with no host or
    path tricks."""
    return isinstance(name, str) and bool(GOOGLE_FONT_NAME_RE.match(name))


def google_font_css_url(name, weight=700):
    """The Google Fonts css2 URL for `name`. `weight` requests that weight, 700
    being the overlay's bold default; pass None to omit it and take the family's
    default face, the fallback for a display font with no 700, where a `:wght@700`
    request 400s. `name` is validated upstream as alphanumerics and spaces, so
    replacing a space with '+' is already URL-safe."""
    spec = (":wght@%d" % weight) if weight else ""
    return ("https://fonts.googleapis.com/css2?family="
            + name.strip().replace(" ", "+") + spec + "&display=swap")


def google_font_cuts_url(name):
    """The css2 URL requesting the four overlay cuts, regular, bold, italic and
    bold-italic, as the named-instance set `ital,wght@0,400;0,700;1,400;1,700`.
    This form NEVER errors: Google returns exactly the cuts the family has, and a
    family with no italic simply omits those blocks."""
    return ("https://fonts.googleapis.com/css2?family="
            + name.strip().replace(" ", "+")
            + ":ital,wght@0,400;0,700;1,400;1,700&display=swap")


def google_font_cut_filename(name, style, weight):
    """Deterministic self-host filename for one downloaded Google cut:
    `<Stem>.woff2` for regular, `<Stem>-Bold.woff2` for 700, `<Stem>-Italic.woff2`
    for italic 400 and `<Stem>-BoldItalic.woff2` for italic 700. The suffix is what
    font_cut() and _font_faces() group back together under the base family."""
    stem = re.sub(r"[^A-Za-z0-9]", "", name)
    bold = str(weight) == "700"
    ital = style == "italic"
    suffix = ("-BoldItalic" if (bold and ital)
              else "-Italic" if ital else "-Bold" if bold else "")
    return stem + suffix + ".woff2"


def parse_google_font_cuts(css):
    """Map {(style, weight): gstatic-woff2-url} for the **latin** subset blocks of a
    css2 response, the blocks whose unicode-range includes U+0000-00FF. Other
    subsets are dropped: the overlay is latin-only and one file per cut keeps the
    self-host small. Only gstatic woff2 URLs are accepted."""
    out = {}
    for block in re.split(r"@font-face", css or "")[1:]:
        if "U+0000-00FF" not in block:                # latin subset only
            continue
        style = re.search(r"font-style:\s*(normal|italic)", block)
        weight = re.search(r"font-weight:\s*(\d+)", block)
        url = re.search(r"url\((https://fonts\.gstatic\.com/[^)]+\.woff2)\)", block)
        if style and weight and url:
            out[(style.group(1), weight.group(1))] = url.group(1)
    return out


# Recognized cut suffixes on a self-hosted font file, longest first so
# "-BoldItalic" wins over "-Italic". Each maps to (font-style, font-weight).
_FONT_CUT_SUFFIXES = (
    ("-bolditalic", ("italic", "700")),
    ("-italic", ("italic", "1 1000")),
    ("-bold", ("normal", "700")),
)


def font_cut(filename):
    """For a self-hosted cut file, return (base_family, font-style, font-weight),
    or None for a plain base file with no recognized suffix, so a hyphenated family
    like 'My-Font' is NOT mis-split. The base cut uses a weight RANGE so a single
    variable roman renders true bold; -Bold and -BoldItalic pin the exact 700
    cut."""
    stem = font_family(filename)
    low = stem.lower()
    for suf, (style, weight) in _FONT_CUT_SUFFIXES:
        if low.endswith(suf) and len(stem) > len(suf):
            return (stem[:-len(suf)], style, weight)
    return None


def _good_font_name(name):
    """A syntactically valid, servable font filename: whitelisted name and a known
    extension."""
    return (isinstance(name, str) and bool(FONT_NAME_RE.match(name)) and "." in name
            and name.rsplit(".", 1)[1].lower() in FONT_EXTS)


def font_families(filenames):
    """Sorted unique family names a picker should offer. Cut siblings collapse onto
    their base family when the base is present, so 'NunitoSans-Italic' is not
    offered as a separate, unmatchable family. A lone sibling with no base stays
    selectable."""
    valid = [n for n in (filenames or []) if _good_font_name(n)]
    stems = {font_family(n) for n in valid}
    out = set()
    for n in valid:
        cut = font_cut(n)
        out.add(cut[0] if (cut and cut[0] in stems) else font_family(n))
    return sorted(out)


def empty_layout(page):
    """A fresh, no-override layout for `page`, preserving the base look."""
    return {"version": 1, "page": page, "slots": {}, "fonts": [], "customCss": ""}


def migrate_layout(page, existing_css):
    """First-use layout for a profile that already has a hand-written <page>.css.
    The CSS is preserved VERBATIM in customCss and never reverse-parsed, so nothing
    is lost, and the slot map starts empty for the operator to build on top."""
    layout = empty_layout(page)
    layout["customCss"] = existing_css or ""
    return layout


def extract_slots(html):
    """Editable slots from a base page's data-edit markers, in document order.
    Each is {id, label, props}. props is the KIND_PROPS set for the element's
    data-edit-kind, with any data-edit-props appended as extras; the explicit
    data-edit-props comma list when no kind is given, for back-compat; else
    DEFAULT_PROPS. The markup is the single source of truth, so there is no
    hardcoded slot list to drift."""
    slots = []
    for tag in re.finditer(r"<[^>]*\bdata-edit=\"[^\"]*\"[^>]*>", html):
        text = tag.group(0)
        mid = re.search(r"\bid=\"([^\"]+)\"", text)
        if not mid:
            continue
        label = re.search(r"\bdata-edit=\"([^\"]*)\"", text).group(1)
        mk = re.search(r"\bdata-edit-kind=\"([^\"]*)\"", text)
        mp = re.search(r"\bdata-edit-props=\"([^\"]*)\"", text)
        extras = [p.strip() for p in mp.group(1).split(",") if p.strip()] if mp else []
        if mk and mk.group(1) in KIND_PROPS:
            props = list(KIND_PROPS[mk.group(1)])
            props += [p for p in extras if p not in props]   # extras appended, de-duped
        elif extras:
            props = extras                                   # back-compat
        else:
            props = list(DEFAULT_PROPS)
        slots.append({"id": mid.group(1), "label": label, "props": props})
    return slots


def base_style(html):
    """Contents of the base page's first <style> block, for the canvas, or ''."""
    m = re.search(r"<style[^>]*>(.*?)</style>", html, re.S)
    return m.group(1).strip() if m else ""


def base_body(html):
    """The static slot markup: the <body> content up to the first <script>, or
    </body>. It carries the slot elements the canvas renders, without page JS."""
    m = re.search(r"<body[^>]*>(.*?)(?:<script|</body>)", html, re.S)
    return m.group(1).strip() if m else ""


# Overlay slot id -> the OBS scene item that slot's box drives, as scene plus
# source name. Only the overlay elements that map to a positioned OBS video source
# appear here: the POV picture-in-picture in endurance and the solo-mode webcam
# frame. Feed A and B are full-screen, and the clock, race control, flags and the
# telemetry panel are pure overlay with no OBS source behind them.
#
# `scene` is where the LIVE sync (SetSceneItemTransform via GetSceneItemId) targets
# the item. The optional `export_scene` scopes the setup-time bake's tree-walk to a
# single scene: the 'Solo Webcam' item is repositioned ONLY where it is embedded in
# 'Program', never in the standalone fullscreen 'Solo Webcam' scene or its device.
# POV omits it and bakes the whole tree, because 'Feed POV' may live in different
# scenes across collections and every instance should track the box.
OVERLAY_SLOT_OBS_SOURCES = {
    "pov":    {"scene": "Stint",   "source": "Feed POV"},
    "webcam": {"scene": "Program", "source": "Solo Webcam", "export_scene": "Program"},
    "tyres-capture": {"scene": "Program", "source": "Solo Tyres/Fuel Capture",
                       "export_scene": "Program"},
}

# The px props we map onto an OBS scene-item transform.
_POV_PX_RE = re.compile(r"\b(left|top|width|height)\s*:\s*(-?\d+(?:\.\d+)?)px")

# Compiled per-slot `#<slot_id>{...}` rule regexes, built on first use and cached.
# The slot set is small and fixed, so this never grows unbounded.
_SLOT_RULE_RE_CACHE = {}


def _slot_rule_re(slot_id):
    """Regex matching `#<slot_id>{...}` rule bodies, but not `#<slot_id>-name` or
    `#<slot_id>foo`: the negative lookahead bars a longer ident or a hyphen after
    the id. `[^{}]*` lets `#<slot_id>.empty` and `#<slot_id>:hover` through to the
    brace."""
    rx = _SLOT_RULE_RE_CACHE.get(slot_id)
    if rx is None:
        rx = re.compile(r"#" + re.escape(slot_id) + r"(?![\w-])[^{}]*\{([^{}]*)\}")
        _SLOT_RULE_RE_CACHE[slot_id] = rx
    return rx


def box_from_css(css_text, slot_id="pov"):
    """Effective #<slot_id> box overrides from override CSS: a dict with any subset
    of {'left','top','width','height'} in px, int or float. Every #<slot_id> rule is
    read in document order, later properties overriding earlier ones like the CSS
    cascade, so a customCss override appended after a generated rule wins. Returns
    an empty dict when the input is not a string, has no #<slot_id> rule, or the
    rule carries no px box props; the caller then applies no transform."""
    if not isinstance(css_text, str):
        return {}
    out = {}
    for body in _slot_rule_re(slot_id).findall(css_text):     # document order
        for key, val in _POV_PX_RE.findall(body):
            f = float(val)
            out[key] = int(f) if f.is_integer() else f
    return out


def pov_box_from_css(css_text):
    """Back-compat wrapper: the #pov box overrides. See box_from_css()."""
    return box_from_css(css_text, "pov")


def _safe_value(value):
    """A style value safe to drop into a generated rule, else None."""
    if isinstance(value, bool):                 # bool is an int subclass, reject
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        v = value.strip()
        return v if v and not _UNSAFE_VALUE.search(v) else None
    return None


def _text_shadow_decl(value):
    """One 'text-shadow: Xpx Ypx Bpx COLOR' from a {x,y,blur,color} dict, or None.
    Each part is validated on its own, the offsets and blur as numbers and the
    color through _safe_value, so no value can inject CSS. It returns None when the
    color is absent or unsafe, or when x, y and blur are all 0."""
    if not isinstance(value, dict):
        return None
    nums = []
    for k in ("x", "y", "blur"):
        n = value.get(k, 0)
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            return None
        nums.append(int(n) if float(n).is_integer() else n)
    color = _safe_value(value.get("color"))
    if not isinstance(color, str) or nums == [0, 0, 0]:
        return None
    return f"text-shadow: {nums[0]}px {nums[1]}px {nums[2]}px {color}"


def _slant_decl(value):
    """A 'clip-path: polygon(...)' parallelogram from a signed px slant, or None.
    The sign is the lean direction, positive leaning '/' and negative '\\', and
    |value| is the horizontal edge offset. Both vertical edges slant equally, so
    text content stays upright. 0, |value| > 400, a non-number or a bool clips
    nothing and returns None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value == 0 or not -400 <= value <= 400:
        return None
    a = abs(value)
    a = int(a) if float(a).is_integer() else a
    if value > 0:
        poly = f"polygon({a}px 0, 100% 0, calc(100% - {a}px) 100%, 0 100%)"
    else:
        poly = f"polygon(0 0, calc(100% - {a}px) 0, 100% 100%, {a}px 100%)"
    return f"clip-path: {poly}"


def _declaration(prop, value):
    """CSS 'name: value' for one (prop, value), or None when it is unsupported or
    unsafe."""
    if prop == "textShadow":
        return _text_shadow_decl(value)
    if prop == "visible":
        # Only an explicit False hides the slot; anything else stays shown. This
        # must precede _safe_value: bool is an int subclass, so _safe_value(False)
        # returns False rather than None and would fall through to a no-op.
        return "display: none" if value is False else None
    if prop == "slant":
        return _slant_decl(value)
    value = _safe_value(value)
    if value is None:
        return None
    if prop in _PX_PROPS:
        if not isinstance(value, (int, float)):
            return None                         # px props take numbers only
        num = int(value) if float(value).is_integer() else value
        return f"{_PX_PROPS[prop]}: {num}px"
    if prop in _TEXT_PROPS:
        return f"{_TEXT_PROPS[prop]}: {value}" if isinstance(value, str) else None
    if prop == "fontFamily":
        return f'font-family: "{value}"' if isinstance(value, str) else None
    if prop == "align":
        mapped = _ALIGN.get(value) if isinstance(value, str) else None
        return f"justify-content: {mapped}" if mapped else None
    if prop == "valign":
        mapped = _VALIGN.get(value) if isinstance(value, str) else None
        return f"align-items: {mapped}" if mapped else None
    if prop == "textTransform":
        mapped = _TEXT_TRANSFORM.get(value) if isinstance(value, str) else None
        return f"text-transform: {mapped}" if mapped else None
    if prop == "fontWeight":
        mapped = _FONT_WEIGHT.get(value) if isinstance(value, str) else None
        return f"font-weight: {mapped}" if mapped else None
    if prop == "fontStyle":
        mapped = _FONT_STYLE.get(value) if isinstance(value, str) else None
        return f"font-style: {mapped}" if mapped else None
    if prop == "opacity":
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            return None
        num = int(value) if float(value).is_integer() else value
        return f"opacity: {num}"
    if prop == "lineHeight":
        if not isinstance(value, (int, float)) or not 0 < value <= 5:
            return None
        num = int(value) if float(value).is_integer() else value
        return f"line-height: {num}"
    return None


def _num_in_range(value, lo, hi):
    """A normalized number, int when integral, if `value` is a real number in
    [lo, hi], else None. A bool is rejected, being an int subclass."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not lo <= value <= hi:
        return None
    return int(value) if float(value).is_integer() else value


def _transform_decl(overrides, allowed):
    """One combined 'transform: rotate(Rdeg) skewX(Kdeg)' from a slot's rotation and
    shear overrides, each gated by `allowed` and its range, or None when neither
    applies. Merging both into a SINGLE declaration stops one transform from
    silently overriding the other, since of two `transform:` lines the later
    wins."""
    parts = []
    if "rotation" in allowed:
        r = _num_in_range(overrides.get("rotation"), -360, 360)
        if r is not None:
            parts.append(f"rotate({r}deg)")
    if "shear" in allowed:
        k = _num_in_range(overrides.get("shear"), -89, 89)
        if k is not None:
            parts.append(f"skewX({k}deg)")
    return f"transform: {' '.join(parts)}" if parts else None


def _slot_rule(slot_id, overrides, allowed):
    """A '#id { ... }' rule for one slot's overrides, gated by its allowed props.
    rotation and shear are emitted together as one combined transform by
    _transform_decl, so they are skipped in the per-prop loop."""
    decls = []
    for prop in PROP_ORDER:
        if prop in ("rotation", "shear"):
            continue
        if prop not in allowed or prop not in overrides:
            continue
        decl = _declaration(prop, overrides[prop])
        if decl:
            decls.append(decl)
    tdecl = _transform_decl(overrides, allowed)
    if tdecl:
        decls.append(tdecl)
    if not decls:
        return ""
    return f"#{slot_id} {{ {'; '.join(decls)}; }}\n"


def _font_faces(fonts):
    """@font-face blocks for the valid fonts the layout references. A file carrying
    a cut suffix (-Bold, -Italic, -BoldItalic) groups under its base family and
    emits font-style and font-weight descriptors, so a self-hosted family renders
    TRUE bold and TRUE italic instead of the browser synthesizing them. A single
    base file with no cut sibling stays a descriptor-less face."""
    valid = [n for n in (fonts or []) if _good_font_name(n)]
    groups = {}                       # base family -> [(filename, style, weight)]
    plain = []                        # (stem, filename) with no recognized cut suffix
    for n in valid:
        cut = font_cut(n)
        if cut:
            groups.setdefault(cut[0], []).append((n, cut[1], cut[2]))
        else:
            plain.append((font_family(n), n))
    # A plain file whose stem matches a group is that group's base.
    absorbed = set()
    for stem, n in plain:
        if stem in groups:
            groups[stem].insert(0, (n, "normal", "1 1000"))
            absorbed.add(n)
    out = []
    for stem, n in plain:
        if n in absorbed:
            continue
        out.append(f'@font-face {{ font-family: "{stem}"; '
                   f"src: url(/overlay/fonts/{n}); }}\n")
    for base, members in groups.items():
        for n, style, weight in members:
            out.append(f'@font-face {{ font-family: "{base}"; '
                       f"src: url(/overlay/fonts/{n}); "
                       f"font-style: {style}; font-weight: {weight}; }}\n")
    return out


def compile_overlay_css(layout, slots):
    """Compile a layout model into override CSS for `slots`, the authoritative list
    from the base page. Order: @font-face, an optional global body font, the
    per-slot rules in document order, then customCss appended verbatim last.
    Unknown slot ids, disallowed props and bad fonts are dropped; only the
    customCss escape hatch passes through unfiltered."""
    layout = layout or {}
    allowed = {s["id"]: set(s.get("props") or ()) for s in slots}
    overrides = layout.get("slots") or {}
    out = _font_faces(layout.get("fonts"))

    body_font = _safe_value(layout.get("bodyFont"))
    if isinstance(body_font, str):
        out.append(f'html, body {{ font-family: "{body_font}"; }}\n')

    for slot in slots:                          # document order, not dict order
        sid = slot["id"]
        if sid in overrides and isinstance(overrides[sid], dict):
            rule = _slot_rule(sid, overrides[sid], allowed.get(sid, set()))
            if rule:
                out.append(rule)

    custom = layout.get("customCss")
    if isinstance(custom, str) and custom.strip():
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(custom)
    return "".join(out)
