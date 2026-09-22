#!/usr/bin/env python3
"""Check intra-wiki links and anchors in src/docs/wiki/.

Heading renames silently break [text](Page#anchor) links. This tool builds the
page -> anchors map with GitHub's anchor algorithm and reports links pointing at
missing pages or anchors.

Checked:   [text](Page) · [text](Page#anchor) · [text](#anchor)
Ignored:   schemes (https:, mailto:), image embeds ![…](…), and relative
           file targets containing '/' (e.g. images/…).

Usage:
  python3 tools/check-wiki-links.py            # checks src/docs/wiki/
  python3 tools/check-wiki-links.py some/dir   # checks another directory

Exit 1 when broken links are found. Gates: tests/test_wiki.py runs this over
the real wiki in the suite (= CI); tools/sync-wiki.py runs it before pushing.
"""
import html, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
# inline links, image embeds excluded via lookbehind; optional "title" allowed
LINK_RE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
SCHEME_RE = re.compile(r"[a-z][a-z0-9+.-]*:")
_MD_DECOR = re.compile(r"[*_`]")        # emphasis/code markers in heading text
_ANCHOR_DROP = re.compile(r"[^\w\- ]")  # GitHub keeps word chars, '-', spaces


def github_anchor(heading, seen=None):
    """GitHub's heading -> anchor id; `seen` (dict) makes duplicates -1, -2…
    HTML entities are unescaped first: the wiki source writes '&amp;' literally,
    GitHub renders '&' and anchors the rendered text."""
    text = _MD_DECOR.sub("", html.unescape(heading.strip())).lower()
    text = _ANCHOR_DROP.sub("", text).replace(" ", "-")
    if seen is None:
        return text
    n = seen.get(text)
    seen[text] = (n or 0) + 1
    return text if n is None else f"{text}-{n}"


def _content_lines(markdown):
    """(line_number, line) pairs outside fenced code blocks."""
    fence = None
    for i, line in enumerate(markdown.splitlines(), 1):
        stripped = line.lstrip()
        marker = stripped[:3]
        if marker in ("```", "~~~"):
            if fence is None:
                fence = marker
            elif marker == fence:
                fence = None
            continue
        if fence is None:
            yield i, line


def extract_headings(markdown):
    """ATX heading texts in order, fenced code blocks skipped."""
    heads = []
    for _, line in _content_lines(markdown):
        h = HEADING_RE.match(line)
        if h:
            heads.append(h.group(2))
    return heads


def page_anchors(markdown):
    """Every anchor id the page provides (duplicates suffixed like GitHub)."""
    seen = {}
    return {github_anchor(h, seen) for h in extract_headings(markdown)}


def extract_links(markdown):
    """(line_number, target) for every inline link outside code fences."""
    links = []
    for i, line in _content_lines(markdown):
        for match in LINK_RE.finditer(line):
            links.append((i, match.group(1)))
    return links


def check_wiki(directory):
    """List of '<file>:<line>: …' problems for intra-wiki links in `directory`."""
    docs = {}
    for name in sorted(os.listdir(directory)):
        if name.endswith(".md"):
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                docs[name] = fh.read()
    anchors = {name[:-3]: page_anchors(md) for name, md in docs.items()}
    errors = []
    for name, md in docs.items():
        for line, target in extract_links(md):
            if SCHEME_RE.match(target) or "/" in target:
                continue  # external link or relative file (images/…)
            page, _, anchor = target.partition("#")
            if page and page not in anchors:
                errors.append(f"{name}:{line}: link to missing page "
                              f"'{page}' ({target})")
                continue
            have = anchors[page or name[:-3]]
            if anchor and anchor not in have:
                errors.append(f"{name}:{line}: broken anchor '{target}'")
    return errors


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    directory = args[0] if args else os.path.join(ROOT, "src", "docs", "wiki")
    if not os.path.isdir(directory):
        sys.exit(f"not a directory: {directory}")
    errors = check_wiki(directory)
    for e in errors:
        print(e)
    if errors:
        sys.exit(1)
    print(f"wiki links OK ({os.path.relpath(directory, ROOT)})")


if __name__ == "__main__":
    main()
