"""Build docs/user-guide/USER_GUIDE.html from USER_GUIDE.md: one self-contained, printable HTML file.

    .venv\\Scripts\\python.exe tools\\build_user_guide.py [source.md] [output.html]

Images are embedded as base64 data URIs, so the HTML file can be opened from anywhere (the application opens it
with the Help button / F1). No third-party packages: a small converter handles the Markdown subset the guide uses
- headings (# to ####, with GitHub-style ids), paragraphs, unordered and ordered lists (one level, with indented
continuation paragraphs), tables, images on their own line (rendered as figures with a caption), bold, italic,
inline code, links and "> **Note:** / **Tip:** / **Warning:**" blockquotes.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "docs" / "user-guide" / "USER_GUIDE.md"
DEFAULT_OUTPUT = ROOT / "docs" / "user-guide" / "USER_GUIDE.html"


# =========================================================================== inline formatting
_CODE = re.compile(r"`([^`]+)`")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")


def slugify(text: str) -> str:
    """GitHub-style anchor: lower case, punctuation removed, spaces to hyphens."""
    text = re.sub(r"<[^>]+>", "", text).lower()
    text = re.sub(r"[^\w\s-]", "", text)
    return re.sub(r"\s", "-", text.strip())


def inline(text: str) -> str:
    """Markdown inline syntax to HTML (input is raw Markdown text, output is escaped HTML)."""
    codes: list[str] = []

    def keep_code(m: re.Match) -> str:
        codes.append(f"<code>{html.escape(m.group(1), quote=False)}</code>")
        return f"\x00{len(codes) - 1}\x00"

    text = _CODE.sub(keep_code, text)
    text = html.escape(text, quote=False)
    text = _LINK.sub(lambda m: _link(m.group(1), m.group(2)), text)
    text = _BOLD.sub(r"<strong>\1</strong>", text)
    text = _ITALIC.sub(r"<em>\1</em>", text)
    return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], text)


def _link(label: str, href: str) -> str:
    href_attr = html.escape(html.unescape(href), quote=True)
    if href.startswith(("http://", "https://")):
        return f'<a href="{href_attr}" rel="noopener noreferrer">{label}</a>'
    return f'<a href="{href_attr}">{label}</a>'


# =========================================================================== blocks
@dataclass
class Heading:
    level: int
    text: str
    anchor: str


@dataclass
class Document:
    title: str = ""
    headings: list[Heading] = field(default_factory=list)
    body: list[str] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


NOTE_KINDS = {"note": "Note", "tip": "Tip", "warning": "Warning", "important": "Important"}


class Converter:
    def __init__(self, source: Path):
        self.source = source
        self.base = source.parent
        self.doc = Document()
        self._anchors: set[str] = set()

    # ---- helpers
    def _anchor(self, text: str) -> str:
        base = slugify(text) or "section"
        anchor, n = base, 1
        while anchor in self._anchors:
            anchor = f"{base}-{n}"
            n += 1
        self._anchors.add(anchor)
        return anchor

    def _image(self, alt: str, src: str) -> str:
        path = (self.base / src).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"Image not found: {src} (referenced in {self.source.name})")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        data = base64.b64encode(path.read_bytes()).decode("ascii")
        self.doc.images.append(src)
        alt_attr = html.escape(alt, quote=True)
        return (f'<figure><img src="data:{mime};base64,{data}" alt="{alt_attr}" loading="lazy">'
                f"<figcaption>{inline(alt)}</figcaption></figure>")

    # ---- main
    def convert(self) -> Document:
        lines = self.source.read_text(encoding="utf-8").splitlines()
        i, n = 0, len(lines)
        out = self.doc.body
        while i < n:
            line = lines[i]
            stripped = line.strip()
            if not stripped:
                i += 1
                continue

            # Heading
            m = re.match(r"^(#{1,4})\s+(.*?)\s*#*$", stripped)
            if m:
                level, text = len(m.group(1)), m.group(2)
                if level == 1 and not self.doc.title:
                    self.doc.title = text
                    out.append(f"<h1>{inline(text)}</h1>")
                else:
                    anchor = self._anchor(text)
                    self.doc.headings.append(Heading(level, text, anchor))
                    out.append(f'<h{level} id="{anchor}">{inline(text)}'
                               f'<a class="anchor" href="#{anchor}" aria-label="Link to this section">#</a>'
                               f"</h{level}>")
                i += 1
                continue

            # Image on its own line -> figure
            m = _IMAGE.fullmatch(stripped)
            if m:
                out.append(self._image(m.group(1), m.group(2)))
                i += 1
                continue

            # Blockquote -> note box
            if stripped.startswith(">"):
                quote = []
                while i < n and lines[i].strip().startswith(">"):
                    quote.append(lines[i].strip()[1:].strip())
                    i += 1
                out.append(self._note(" ".join(quote)))
                continue

            # Table
            if stripped.startswith("|") and i + 1 < n and re.match(r"^\|?\s*:?-{3,}", lines[i + 1].strip()):
                rows = []
                while i < n and lines[i].strip().startswith("|"):
                    rows.append(lines[i].strip())
                    i += 1
                out.append(self._table(rows))
                continue

            # Lists
            m = re.match(r"^(\d+)\.\s+(.*)$", stripped) if not line.startswith(" ") else None
            if m or re.match(r"^[-*]\s+", stripped) and not line.startswith(" "):
                i = self._list(lines, i)
                continue

            # Paragraph
            para = [stripped]
            i += 1
            while i < n and lines[i].strip() and not self._starts_block(lines[i]):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{self._join(para)}</p>")
        return self.doc

    @staticmethod
    def _join(parts: list[str]) -> str:
        return inline(" ".join(parts))

    @staticmethod
    def _starts_block(line: str) -> bool:
        s = line.strip()
        return bool(re.match(r"^(#{1,4}\s|>|\||!\[|[-*]\s|\d+\.\s)", s)) and not line.startswith("   ")

    def _note(self, text: str) -> str:
        kind = "note"
        m = re.match(r"^\*\*(\w+):\*\*\s*(.*)$", text)
        if m and m.group(1).lower() in NOTE_KINDS:
            kind, text = m.group(1).lower(), m.group(2)
        label = NOTE_KINDS[kind]
        return (f'<aside class="callout callout-{kind}" role="note"><p><strong class="callout-label">{label}</strong> '
                f"{inline(text)}</p></aside>")

    def _table(self, rows: list[str]) -> str:
        def cells(row: str) -> list[str]:
            row = row.strip().strip("|")
            return [c.strip() for c in re.split(r"(?<!\\)\|", row)]

        head, body = cells(rows[0]), [cells(r) for r in rows[2:]]
        parts = ['<div class="table-wrap"><table>', "<thead><tr>"]
        parts += [f'<th scope="col">{inline(c)}</th>' for c in head]
        parts.append("</tr></thead><tbody>")
        for r in body:
            parts.append("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>")
        parts.append("</tbody></table></div>")
        return "".join(parts)

    def _list(self, lines: list[str], i: int) -> int:
        """One-level list. Items may continue on indented lines and contain indented paragraphs."""
        first = lines[i].strip()
        m = re.match(r"^(\d+)\.\s+", first)
        ordered = m is not None
        start = int(m.group(1)) if m else 1
        item_re = re.compile(r"^\d+\.\s+(.*)$" if ordered else r"^[-*]\s+(.*)$")
        items: list[list[list[str]]] = []  # item -> paragraphs -> lines
        n = len(lines)
        while i < n:
            line = lines[i]
            s = line.strip()
            m = item_re.match(s) if not line.startswith(" ") else None
            if m:
                items.append([[m.group(1)]])
                i += 1
                continue
            if not s:
                # blank line: the list continues only with an indented paragraph or another item
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                if j < n and lines[j].startswith("   ") and items:
                    items[-1].append([])
                    i = j
                    continue
                if j < n and item_re.match(lines[j].strip()) and not lines[j].startswith(" "):
                    i = j
                    continue
                break
            if line.startswith(" ") and items:
                items[-1][-1].append(s)
                i += 1
                continue
            break
        tag = "ol" if ordered else "ul"
        start_attr = f' start="{start}"' if ordered and start != 1 else ""
        html_items = []
        for paragraphs in items:
            paragraphs = [p for p in paragraphs if p]
            if len(paragraphs) == 1:
                html_items.append(f"<li>{self._join(paragraphs[0])}</li>")
            else:
                html_items.append("<li>" + "".join(f"<p>{self._join(p)}</p>" for p in paragraphs) + "</li>")
        self.doc.body.append(f"<{tag}{start_attr}>" + "".join(html_items) + f"</{tag}>")
        return i


# =========================================================================== page
CSS = """
:root{
  --bg:#f5f7fa; --surface:#ffffff; --surface-alt:#eef2f6; --border:#d5dbe3; --text:#18212d; --muted:#566173;
  --accent:#1f5fad; --accent-soft:#e3eefa; --code-bg:#eef2f6;
  --note-bg:#e3eefa; --note-fg:#1a548f; --tip-bg:#e2f3e9; --tip-fg:#17693a; --warn-bg:#fff3d6; --warn-fg:#7a4f00;
  --radius:10px; --measure:46rem;
  color-scheme: light dark;
}
@media (prefers-color-scheme: dark){
  :root{
    --bg:#13161b; --surface:#1b1f26; --surface-alt:#20252d; --border:#2f3640; --text:#e7eaf0; --muted:#a0aaba;
    --accent:#8dbdf5; --accent-soft:#18283b; --code-bg:#20252d;
    --note-bg:#18283b; --note-fg:#8dbdf5; --tip-bg:#16301f; --tip-fg:#5bd08f; --warn-bg:#352a12; --warn-fg:#f2be55;
  }
}
*{box-sizing:border-box}
html{scroll-behavior:smooth;-webkit-text-size-adjust:100%}
@media (prefers-reduced-motion: reduce){html{scroll-behavior:auto}}
body{margin:0;background:var(--bg);color:var(--text);font:16px/1.6 "Segoe UI",system-ui,-apple-system,Roboto,
  "Helvetica Neue",Arial,sans-serif}
a{color:var(--accent);text-underline-offset:2px}
a:focus-visible,summary:focus-visible{outline:3px solid var(--accent);outline-offset:2px;border-radius:4px}
.skip{position:absolute;left:-999px;top:0;background:var(--surface);padding:8px 12px;z-index:10}
.skip:focus{left:16px;top:8px}
.layout{display:grid;grid-template-columns:minmax(0,1fr);gap:24px;max-width:76rem;margin:0 auto;padding:24px 16px 64px}
@media (min-width:1100px){.layout{grid-template-columns:17rem minmax(0,1fr);padding:32px 24px 80px}}
header.cover{grid-column:1/-1;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:24px}
header.cover h1{margin:0 0 4px;font-size:1.9rem;line-height:1.25}
header.cover p{margin:.4em 0;color:var(--muted)}
nav.toc{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;
  font-size:.92rem;align-self:start}
@media (min-width:1100px){nav.toc{position:sticky;top:16px;max-height:calc(100vh - 32px);overflow:auto}}
nav.toc h2{border-top:0;padding-top:0;font-size:.78rem;letter-spacing:.06em;text-transform:uppercase;color:var(--muted);margin:0 0 8px}
nav.toc ol{list-style:none;margin:0;padding:0}
nav.toc ol ol{padding-left:14px;margin:2px 0 6px}
nav.toc li{margin:3px 0}
nav.toc a{text-decoration:none;color:var(--text)}
nav.toc a:hover{color:var(--accent);text-decoration:underline}
main{min-width:0;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);padding:8px 20px 32px}
@media (min-width:700px){main{padding:8px 40px 40px}}
main > *{max-width:var(--measure)}
main > figure,main > .table-wrap{max-width:100%}
h2,h3,h4{line-height:1.3;scroll-margin-top:16px}
h2{font-size:1.45rem;margin:2.2em 0 .6em;padding-top:.6em;border-top:1px solid var(--border)}
main > h2:first-child{border-top:0;margin-top:.8em}
h3{font-size:1.15rem;margin:1.8em 0 .5em}
h4{font-size:1rem;margin:1.4em 0 .4em}
.anchor{margin-left:.4em;font-weight:400;color:var(--muted);text-decoration:none;opacity:0}
h2:hover .anchor,h3:hover .anchor,h4:hover .anchor,.anchor:focus{opacity:1}
p,ul,ol{margin:.6em 0}
li{margin:.3em 0}
li > p{margin:.3em 0}
code{font:0.88em/1.4 Consolas,"Cascadia Mono",ui-monospace,monospace;background:var(--code-bg);padding:.1em .35em;
  border-radius:4px;overflow-wrap:anywhere}
figure{margin:1.2em 0 1.6em}
figure img{display:block;max-width:100%;height:auto;border:1px solid var(--border);border-radius:8px;
  box-shadow:0 1px 3px rgba(0,0,0,.08)}
figcaption{margin-top:.5em;font-size:.9rem;color:var(--muted)}
.table-wrap{overflow-x:auto;margin:1em 0;border:1px solid var(--border);border-radius:8px}
table{border-collapse:collapse;width:100%;font-size:.94rem}
th,td{text-align:left;vertical-align:top;padding:.55em .8em;border-bottom:1px solid var(--border)}
thead th{background:var(--surface-alt);font-weight:600}
tbody tr:last-child td{border-bottom:0}
.callout{margin:1.1em 0;padding:.7em 1em;border-radius:8px;border-left:4px solid var(--note-fg);
  background:var(--note-bg);color:var(--text)}
.callout p{margin:0}
.callout-label{color:var(--note-fg);margin-right:.3em}
.callout-tip{background:var(--tip-bg);border-left-color:var(--tip-fg)}
.callout-tip .callout-label{color:var(--tip-fg)}
.callout-warning,.callout-important{background:var(--warn-bg);border-left-color:var(--warn-fg)}
.callout-warning .callout-label,.callout-important .callout-label{color:var(--warn-fg)}
footer{grid-column:1/-1;color:var(--muted);font-size:.85rem;text-align:center}
@media print{
  :root{--bg:#fff;--surface:#fff;--text:#000;--muted:#444;--border:#bbb;--accent:#000}
  body{font-size:11pt;background:#fff}
  .layout{display:block;max-width:none;padding:0}
  header.cover,nav.toc,main{border:0;padding:0;background:none}
  nav.toc{page-break-after:always;margin-bottom:1em}
  .skip,.anchor{display:none}
  main > *{max-width:none}
  h2{page-break-before:always;border-top:0;margin-top:0}
  main > h2:first-child{page-break-before:avoid}
  h2,h3,h4{page-break-after:avoid}
  figure,table,.callout,tr{page-break-inside:avoid}
  figure img{box-shadow:none;max-height:22cm;width:auto}
  a{color:inherit;text-decoration:none}
}
"""


def render(doc: Document) -> str:
    toc: list[str] = ['<nav class="toc" aria-label="Table of contents"><h2>Contents</h2><ol>']
    open_sub = False
    for h in doc.headings:
        if h.level == 2:
            if open_sub:
                toc.append("</ol></li>")
                open_sub = False
            elif len(toc) > 1:
                toc.append("</li>")
            toc.append(f'<li><a href="#{h.anchor}">{inline(h.text)}</a>')
        elif h.level == 3:
            if not open_sub:
                toc.append("<ol>")
                open_sub = True
            toc.append(f'<li><a href="#{h.anchor}">{inline(h.text)}</a></li>')
    if open_sub:
        toc.append("</ol>")
    toc.append("</li></ol></nav>")

    body = list(doc.body)
    intro: list[str] = []
    if body and body[0].startswith("<h1>"):
        body.pop(0)
    # Paragraphs before the first h2 belong to the cover
    while body and not body[0].startswith("<h2"):
        intro.append(body.pop(0))
    title = html.escape(re.sub(r"[*`]", "", doc.title or "User Guide"))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>{CSS}</style>
</head>
<body>
<a class="skip" href="#content">Skip to the guide</a>
<div class="layout">
<header class="cover"><h1>{inline(doc.title)}</h1>{''.join(intro)}</header>
{''.join(toc)}
<main id="content" tabindex="-1">
{chr(10).join(body)}
</main>
<footer>Oracle EPM Task Manager – Schedule Deployer · User Guide · Press F1 in the application to open this guide.</footer>
</div>
</body>
</html>
"""


def build(source: Path = DEFAULT_SOURCE, output: Path = DEFAULT_OUTPUT) -> Path:
    doc = Converter(source).convert()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(doc), encoding="utf-8", newline="\n")
    size_kb = output.stat().st_size / 1024
    print(f"Wrote {output} ({size_kb:.0f} KB, {len(doc.headings)} headings, {len(doc.images)} images)")
    return output


def main(argv: list[str]) -> int:
    source = Path(argv[1]) if len(argv) > 1 else DEFAULT_SOURCE
    output = Path(argv[2]) if len(argv) > 2 else DEFAULT_OUTPUT
    try:
        build(source, output)
    except (OSError, ValueError) as exc:
        print(f"User guide not built: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
