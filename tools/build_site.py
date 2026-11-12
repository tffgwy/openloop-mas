"""Generate the GitHub Pages documentation site.

The site is plain static HTML generated from the Markdown docs, so it needs
no build tool, no Node and no theme dependency. That keeps the project
installable and forkable by beginners: `python tools/build_site.py` is
enough to regenerate everything.
"""

from __future__ import annotations

import html
import pathlib
import re
from typing import List, Tuple

ROOT = pathlib.Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

PAGES: List[Tuple[str, str, str]] = [
    ("index.html", "首页 Home", "README.md"),
    ("quickstart.html", "快速上手 Quickstart", "docs/quickstart.md"),
    ("architecture.html", "架构 Architecture", "docs/architecture.md"),
    ("loop-engineering.html", "方法论 Loop Engineering", "docs/loop-engineering.md"),
    ("mcp.html", "MCP 接入", "docs/mcp.md"),
    ("acceptance.html", "验收报告 Acceptance", "docs/acceptance-report.md"),
    ("readme-en.html", "README (English)", "README.en.md"),
]

CSS = """
:root {
  --bg: #0f1115;
  --panel: #171a21;
  --text: #e6e8ee;
  --muted: #9aa3b2;
  --accent: #6ea8fe;
  --border: #262b36;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 16px/1.7 -apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
}
.layout { display: flex; min-height: 100vh; }
nav {
  width: 240px; flex: 0 0 240px; padding: 24px 16px;
  background: var(--panel); border-right: 1px solid var(--border);
}
nav h1 { font-size: 15px; margin: 0 0 4px; letter-spacing: .5px; }
nav .ver { color: var(--muted); font-size: 12px; margin-bottom: 20px; }
nav a {
  display: block; padding: 7px 10px; margin-bottom: 2px;
  color: var(--muted); text-decoration: none; border-radius: 6px; font-size: 14px;
}
nav a:hover { background: #1f2430; color: var(--text); }
nav a.on { background: #223050; color: var(--accent); }
main { flex: 1; padding: 40px 48px 80px; max-width: 980px; }
main h1 { font-size: 30px; margin-top: 0; }
main h2 { font-size: 22px; margin-top: 36px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
main h3 { font-size: 17px; margin-top: 26px; }
main code { background: #1d222c; padding: 2px 6px; border-radius: 4px; font-size: 13px; }
main pre {
  background: #14171e; border: 1px solid var(--border); border-radius: 8px;
  padding: 14px 16px; overflow-x: auto; font-size: 13px; line-height: 1.55;
}
main pre code { background: none; padding: 0; }
main table { border-collapse: collapse; width: 100%; margin: 18px 0; font-size: 14px; }
main th, main td { border: 1px solid var(--border); padding: 8px 11px; text-align: left; }
main th { background: var(--panel); }
main blockquote {
  margin: 16px 0; padding: 10px 16px;
  border-left: 3px solid var(--accent); background: #141a24; color: #c6cddb;
}
main a { color: var(--accent); }
main li { margin: 4px 0; }
main hr { border: 0; border-top: 1px solid var(--border); margin: 32px 0; }
footer { color: var(--muted); font-size: 13px; margin-top: 60px;
  padding-top: 20px; border-top: 1px solid var(--border); }
@media (max-width: 820px) {
  .layout { flex-direction: column; }
  nav { width: auto; flex: none; border-right: 0; border-bottom: 1px solid var(--border); }
  main { padding: 26px 20px 60px; }
}
"""


def inline(text: str) -> str:
    """Inline Markdown formatting."""
    placeholders: List[str] = []

    def stash(value: str) -> str:
        placeholders.append(value)
        return "\x00%d\x00" % (len(placeholders) - 1)

    def code_sub(match: "re.Match") -> str:
        return stash("<code>%s</code>" % html.escape(match.group(1)))

    text = re.sub(r"`([^`]+)`", code_sub, text)
    text = re.sub(
        r"!\[([^\]]*)\]\(([^)]+)\)",
        lambda m: stash('<img src="%s" alt="%s">' % (m.group(2), m.group(1))),
        text,
    )
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: stash('<a href="%s">%s</a>' % (m.group(2), m.group(1))),
        text,
    )
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", text)
    for index, value in enumerate(placeholders):
        text = text.replace("\x00%d\x00" % index, value)
    return text


def md_to_html(markdown: str) -> str:
    """A small, dependency-free Markdown to HTML converter.

    It handles the subset the project docs actually use: headings, fenced
    code blocks, tables, lists, blockquotes, horizontal rules, bold, italics,
    inline code and links.
    """
    lines = markdown.split("\n")
    out: List[str] = []
    i = 0
    in_code = False
    in_table = False
    list_stack: List[str] = []

    def close_list() -> None:
        while list_stack:
            out.append("</%s>" % list_stack.pop())

    def close_table() -> None:
        nonlocal in_table
        if in_table:
            out.append("</tbody></table>")
            in_table = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                close_list()
                close_table()
                out.append("<pre><code>")
                in_code = True
            i += 1
            continue
        if in_code:
            out.append(html.escape(line))
            i += 1
            continue

        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if re.fullmatch(r"[:\- ]+", stripped.strip("|").replace("|", "")):
                i += 1
                continue
            if not in_table:
                close_list()
                out.append("<table><thead><tr>")
                out.append("".join("<th>%s</th>" % inline(c) for c in cells))
                out.append("</tr></thead><tbody>")
                in_table = True
            else:
                out.append("<tr>" + "".join("<td>%s</td>" % inline(c) for c in cells) + "</tr>")
            i += 1
            continue
        close_table()

        match = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if match:
            close_list()
            level = len(match.group(1))
            out.append("<h%d>%s</h%d>" % (level, inline(match.group(2)), level))
            i += 1
            continue

        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", stripped):
            close_list()
            out.append("<hr>")
            i += 1
            continue

        if stripped.startswith(">"):
            close_list()
            out.append("<blockquote>%s</blockquote>" % inline(stripped.lstrip(">").strip()))
            i += 1
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        numbered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet or numbered:
            want = "ul" if bullet else "ol"
            if not list_stack or list_stack[-1] != want:
                close_list()
                list_stack.append(want)
                out.append("<%s>" % want)
            out.append("<li>%s</li>" % inline((bullet or numbered).group(1)))
            i += 1
            continue
        close_list()

        if not stripped:
            i += 1
            continue

        out.append("<p>%s</p>" % inline(stripped))
        i += 1

    close_list()
    close_table()
    if in_code:
        out.append("</code></pre>")
    return "\n".join(out)


def page(title: str, body: str, active: str, version: str) -> str:
    nav_items = []
    for filename, label, _ in PAGES:
        cls = ' class="on"' if filename == active else ""
        nav_items.append('<a href="%s"%s>%s</a>' % (filename, cls, html.escape(label)))
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%s · OpenLoop MAS</title>
<link rel="stylesheet" href="style.css">
</head>
<body>
<div class="layout">
<nav>
  <h1>OpenLoop MAS</h1>
  <div class="ver">v%s · loop engineering runtime</div>
  %s
</nav>
<main>
%s
<footer>
  MIT licensed. Source: <a href="https://github.com/tffgwy/openloop-mas">github.com/tffgwy/openloop-mas</a>
</footer>
</main>
</div>
</body>
</html>
""" % (html.escape(title), html.escape(version), "".join(nav_items), body)


def main() -> int:
    version = "0.2.0"
    init_file = ROOT / "mas" / "__init__.py"
    if init_file.exists():
        match = re.search(r'__version__\s*=\s*"([^"]+)"', init_file.read_text(encoding="utf-8"))
        if match:
            version = match.group(1)

    SITE.mkdir(parents=True, exist_ok=True)
    written = []
    for filename, label, source in PAGES:
        source_path = ROOT / source
        if not source_path.exists():
            continue
        body = md_to_html(source_path.read_text(encoding="utf-8"))
        (SITE / filename).write_text(page(label, body, filename, version), encoding="utf-8")
        written.append(filename)

    (SITE / "style.css").write_text(CSS, encoding="utf-8")
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    (SITE / "sitemap.xml").write_text(
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(
            "  <url><loc>https://tffgwy.github.io/openloop-mas/%s</loc></url>\n" % f
            for f in written
            if f != "index.html"
        )
        + "</urlset>\n",
        encoding="utf-8",
    )

    print("site built: %d pages -> %s" % (len(written), SITE))
    for filename in written:
        print("  " + filename)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
