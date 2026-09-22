"""Minimal, dependency-free Markdown -> HTML for the Control Center's Help page.

Renders the bundled operator docs (headings, tables, ordered/unordered lists,
fenced code, blockquotes, inline bold/italic/code/links) as a styled page so
they read like a real document instead of raw text in a browser tab. Stdlib
only; the input is trusted (our own docs) but text is still HTML-escaped.
Tests: tests/test_mdrender.py."""
import html
import re

_LIST_RE = re.compile(r"^(\s*)([-*+]|\d+\.)\s+(.*)$")
_SAFE_SCHEMES = ("http://", "https://", "mailto:", "/", "#", "./", "../")


def _safe_href(url):
    """Sanitize a link target for untrusted Markdown (e.g. GitHub release notes).
    Returns the href to emit, or '' to drop the link. Allows http(s)/mailto and
    relative/anchor targets; rejects javascript:/data:/other schemes. The URL has
    already been HTML-escaped with quote=False, so `&<>` are safe here — only the
    attribute-breakout char `\"` must still be neutralised."""
    low = url.strip().lower()
    safe = low.startswith(_SAFE_SCHEMES) or ":" not in low.split("/", 1)[0]
    if not safe:
        return ""
    return url.replace('"', "&quot;")


def _link(m):
    href = _safe_href(m.group(2))
    if not href:                                 # unsafe scheme -> link text only
        return m.group(1)
    return f'<a href="{href}" target="_blank" rel="noopener">{m.group(1)}</a>'


def _autolink(m):
    """Markdown autolink <url>: the URL is both the target and the link text. By
    this point '<'/'>' are already escaped to &lt;/&gt; (group 1 is the inner URL,
    still HTML-escaped)."""
    url = m.group(1)
    href = _safe_href(url)
    if not href:                                 # unsafe scheme -> leave as written
        return m.group(0)
    return f'<a href="{href}" target="_blank" rel="noopener">{url}</a>'


def _inline(s):
    """Inline formatting for one line of text (already plain Markdown)."""
    s = html.escape(s, quote=False)
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    s = re.sub(r"`([^`]+)`", stash, s)                       # protect inline code
    s = re.sub(r"!?\[([^\]]+)\]\(([^)\s]+)\)", _link, s)      # links (images -> link)
    s = re.sub(r"&lt;((?:https?://|mailto:)[^\s]+?)&gt;", _autolink, s)  # <url> autolinks
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"__(.+?)__", r"<strong>\1</strong>", s)
    s = re.sub(r"\*([^*\n]+)\*", r"<em>\1</em>", s)          # only *italic* (not _ -> snake_case safe)
    s = re.sub(r"\x00(\d+)\x00",
               lambda m: f"<code>{codes[int(m.group(1))]}</code>", s)
    return s


def _row_cells(line):
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _is_table_sep(line):
    return ("-" in line and "|" in line
            and bool(re.match(r"^\s*\|?[\s:\-|]+\|?\s*$", line)))


def _aligns(sep):
    out = []
    for c in _row_cells(sep):
        left, right = c.startswith(":"), c.endswith(":")
        # left is the default -> no explicit style; only center/right are emitted
        out.append("center" if left and right else "right" if right else "")
    return out


def _cell(tag, text, align):
    style = f' style="text-align:{align}"' if align else ""
    return f"<{tag}{style}>{_inline(text)}</{tag}>"


def _table(header, aligns, rows):
    def al(j):
        return aligns[j] if j < len(aligns) else ""
    head = "".join(_cell("th", c, al(j)) for j, c in enumerate(header))
    body = "".join("<tr>" + "".join(_cell("td", c, al(j)) for j, c in enumerate(r))
                   + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _parse_list_items(lines):
    """Flatten a list block into [indent, ordered, content] (continuation lines
    fold into the preceding item)."""
    items = []
    for ln in lines:
        m = _LIST_RE.match(ln)
        if m:
            items.append([len(m.group(1)), m.group(2)[0].isdigit(), m.group(3)])
        elif items and ln.strip():
            items[-1][2] += " " + ln.strip()
    return items


def _render_list(items, i, indent):
    """Build a (possibly nested) <ul>/<ol> from flat items starting at i."""
    tag = "ol" if items[i][1] else "ul"
    out = [f"<{tag}>"]
    while i < len(items) and items[i][0] >= indent:
        ind, _ordered, content = items[i]
        if ind > indent:                       # deeper -> nest into the previous <li>
            sub, i = _render_list(items, i, ind)
            out[-1] = out[-1][:-5] + sub + "</li>"
        else:
            out.append(f"<li>{_inline(content)}</li>")
            i += 1
    out.append(f"</{tag}>")
    return "".join(out), i


def _is_block_start(lines, i):
    line = lines[i]
    if not line.strip():
        return True
    if line.lstrip().startswith("```") or line.lstrip().startswith(">"):
        return True
    if re.match(r"#{1,6}\s", line) or _LIST_RE.match(line):
        return True
    if re.match(r"^\s*(\*\s*){3,}$|^\s*(-\s*){3,}$|^\s*(_\s*){3,}$", line):
        return True
    if "|" in line and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
        return True
    return False


def render(md):
    """Markdown text -> an HTML fragment (no <html>/<body> wrapper)."""
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out, i, n = [], 0, len(lines)
    while i < n:
        line = lines[i]
        if line.lstrip().startswith("```"):                 # fenced code
            i += 1
            code = []
            while i < n and not lines[i].lstrip().startswith("```"):
                code.append(lines[i])
                i += 1
            i += 1                                          # skip closing fence
            out.append("<pre><code>"
                       + html.escape("\n".join(code), quote=False)
                       + "</code></pre>")
            continue
        if not line.strip():
            i += 1
            continue
        m = re.match(r"(#{1,6})\s+(.*?)\s*#*\s*$", line)     # heading
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{_inline(m.group(2))}</h{lvl}>")
            i += 1
            continue
        if re.match(r"^\s*(\*\s*){3,}$|^\s*(-\s*){3,}$|^\s*(_\s*){3,}$", line):
            out.append("<hr>")
            i += 1
            continue
        if "|" in line and i + 1 < n and _is_table_sep(lines[i + 1]):   # table
            header, aligns = _row_cells(line), _aligns(lines[i + 1])
            i += 2
            rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                rows.append(_row_cells(lines[i]))
                i += 1
            out.append(_table(header, aligns, rows))
            continue
        if line.lstrip().startswith(">"):                   # blockquote
            quote = []
            while i < n and lines[i].lstrip().startswith(">"):
                quote.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + render("\n".join(quote)) + "</blockquote>")
            continue
        if _LIST_RE.match(line):                            # list
            block = []
            while i < n and (_LIST_RE.match(lines[i])
                             or (lines[i][:1] == " " and lines[i].strip())):
                block.append(lines[i])
                i += 1
            items = _parse_list_items(block)
            if items:
                base = min(it[0] for it in items)
                out.append(_render_list(items, 0, base)[0])
            continue
        para = []                                           # paragraph
        while i < n and lines[i].strip() and not _is_block_start(lines, i):
            para.append(lines[i].strip())
            i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")
    return "\n".join(out)


_CSS = """
:root{color-scheme:dark}
*{box-sizing:border-box}
body{margin:0;background:#0F172A;color:#E2E8F0;
 font:15px/1.65 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:820px;margin:0 auto;padding:40px 28px 80px}
h1,h2,h3,h4{color:#F8FAFC;line-height:1.25;margin:1.8em 0 .6em;font-weight:650}
h1{font-size:1.9em;margin-top:.2em;border-bottom:1px solid #273349;padding-bottom:.3em}
h2{font-size:1.4em;border-bottom:1px solid #273349;padding-bottom:.25em}
h3{font-size:1.15em}
a{color:#60A5FA;text-decoration:none}a:hover{text-decoration:underline}
p{margin:.7em 0}
code{background:#1B2336;border:1px solid #273349;border-radius:5px;
 padding:.1em .4em;font:.88em ui-monospace,"SF Mono",Menlo,Consolas,monospace;color:#E2E8F0}
pre{background:#0B1120;border:1px solid #273349;border-radius:10px;
 padding:14px 16px;overflow:auto;line-height:1.5}
pre code{background:none;border:0;padding:0;color:#CBD5E1}
ul,ol{margin:.6em 0;padding-left:1.5em}li{margin:.25em 0}
blockquote{margin:.8em 0;padding:.4em 1em;border-left:3px solid #3B82F6;
 background:#151C2E;border-radius:0 8px 8px 0;color:#CBD5E1}
table{border-collapse:collapse;width:100%;margin:1em 0;font-size:.93em;
 border:1px solid #273349;border-radius:8px;overflow:hidden}
th,td{border:1px solid #273349;padding:7px 11px;text-align:left;vertical-align:top}
thead th{background:#1B2336;color:#F8FAFC;font-weight:650}
tbody tr:nth-child(even){background:#151C2E}
hr{border:0;border-top:1px solid #273349;margin:2em 0}
"""


def page(title, body_html):
    """Wrap a rendered fragment in a self-contained, dark-themed HTML document."""
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title)}</title><style>{_CSS}</style></head>"
            f"<body><main>{body_html}</main></body></html>")
