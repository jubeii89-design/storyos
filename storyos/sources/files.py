"""PDF, DOCX, Markdown, plain text and web pages, all becoming Markdown.

Each converter keeps structure where it can, because the library's value is in
being readable and pinnable, and a wall of undifferentiated text is neither.
Headings survive; page furniture does not.
"""

from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

SUPPORTED = (".pdf", ".docx", ".md", ".markdown", ".txt", ".html", ".htm")


class Unsupported(ValueError):
    pass


def is_url(s: str) -> bool:
    p = urllib.parse.urlparse(s.strip())
    return p.scheme in ("http", "https") and bool(p.netloc)


def first_heading(md: str) -> str:
    """The document's own title, if it states one.

    A filename is whatever the file was last saved as -- `craft.docx`,
    `document-2-final.pdf` -- and a library listed by filename is unreadable.
    The first heading is what the author called the thing.
    """
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#"):
            title = s.lstrip("#").strip()
            if 3 <= len(title) <= 90:
                return title
        elif s and not s.startswith("<!--"):
            break          # real text before any heading: there is no title
    return ""


def read(path: str | Path) -> tuple[str, str, str]:
    """Return `(markdown, title, format)` for a local file."""
    p = Path(path).expanduser()
    if not p.is_file():
        raise FileNotFoundError(p)
    ext = p.suffix.lower()
    if ext not in SUPPORTED:
        raise Unsupported(
            f"{ext or 'that file'} is not a format the library reads. "
            f"Supported: {', '.join(SUPPORTED)}")

    if ext == ".pdf":
        md = _pdf(p)
        return md, first_heading(md) or p.stem, "pdf"
    if ext == ".docx":
        md = _docx(p)
        return md, first_heading(md) or p.stem, "docx"
    if ext in (".html", ".htm"):
        md, title = _html(p.read_text(errors="replace"), str(p))
        return md, title or p.stem, "html"
    md = p.read_text(errors="replace")
    return md, first_heading(md) or p.stem, ext.lstrip(".")


def _pdf(p: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise Unsupported("PDF support needs: pip install 'storyos[library]'") from None

    reader = PdfReader(str(p))
    pages = []
    for page in reader.pages:
        # Layout mode keeps the line structure, which the heading heuristic
        # below needs. Plain mode returns a soup in which a heading is
        # indistinguishable from the first words of the paragraph under it.
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            text = page.extract_text() or ""
        text = text.strip()
        if text:
            pages.append(_to_markdown(text))
    # Page markers are deliberate: a craft book cited in a pin should be
    # locatable in the physical copy.
    return "\n\n".join(f"<!-- page {i} -->\n\n{t}"
                       for i, t in enumerate(pages, 1))


SENTENCE_END = (".", "?", "!", ":", ";", ",", "\u2014")


def _looks_like_heading(line: str, nxt: str) -> bool:
    """A short, unpunctuated line above a longer one.

    This is a heuristic and it will be wrong sometimes -- a PDF carries no
    heading semantics at all, only glyph positions, so the alternative is not
    a better answer but no answer. Getting it wrong leaves a stray short
    paragraph; not trying at all merges every heading into the text beneath it,
    which is what the pin anchors cannot survive.
    """
    s = line.strip()
    if not s or len(s) > 70 or s.endswith(SENTENCE_END):
        return False
    if len(s.split()) > 10:
        return False
    return len(nxt.strip()) > len(s)


def _to_markdown(text: str) -> str:
    # Soft hyphens survive extraction and split a word for every later search.
    text = text.replace("\u00ad", "")
    # Known limit: a renderer that breaks on a soft hyphen and drops the glyph
    # leaves "relent" and "less" separated by whitespace and nothing else --
    # indistinguishable from two real words. That case is left alone on
    # purpose, because rejoining on whitespace would corrupt ordinary prose.
    # LaTeX, InDesign and Word all emit a visible hyphen, which is handled.
    text = re.sub(r"(\w)-\n\s*(\w)", r"\1\2", text)     # hard hyphenation
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]

    blocks: list[str] = []
    buf: list[str] = []
    for i, ln in enumerate(lines):
        if not ln:
            if buf: blocks.append(" ".join(buf)); buf = []
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip()), "")
        if _looks_like_heading(ln, nxt):
            if buf: blocks.append(" ".join(buf)); buf = []
            blocks.append("## " + ln)
        else:
            buf.append(ln)
    if buf: blocks.append(" ".join(buf))
    return "\n\n".join(blocks)


def _docx(p: Path) -> str:
    try:
        import docx
    except ImportError:
        raise Unsupported("DOCX support needs: pip install 'storyos[library]'") from None

    doc = docx.Document(str(p))
    out = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = (para.style.name or "").lower()
        if style.startswith("heading"):
            level = "".join(c for c in style if c.isdigit()) or "1"
            out.append("#" * min(6, int(level)) + " " + text)
        elif style.startswith("list"):
            out.append("- " + text)
        else:
            out.append(text)
    return "\n\n".join(out)


def _html(raw: str, source: str) -> tuple[str, str]:
    try:
        import trafilatura
    except ImportError:
        raise Unsupported("Web support needs: pip install 'storyos[library]'") from None

    md = trafilatura.extract(raw, output_format="markdown",
                             include_links=True, include_comments=False,
                             include_tables=True, url=source)
    title = ""
    meta = trafilatura.extract_metadata(raw)
    if meta and meta.title:
        title = meta.title
    if not md:
        raise Unsupported(f"nothing readable could be extracted from {source}")
    return md, title


def fetch_url(url: str) -> tuple[str, str, str]:
    """A web page as Markdown. Trafilatura strips nav, ads and boilerplate."""
    try:
        import trafilatura
    except ImportError:
        raise Unsupported("Web support needs: pip install 'storyos[library]'") from None

    raw = trafilatura.fetch_url(url)
    if not raw:
        raise RuntimeError(f"could not fetch {url}")
    md, title = _html(raw, url)
    return md, title or urllib.parse.urlparse(url).path.strip("/") or url, "web"


# -- preserving the original ---------------------------------------------

CHROMIUM = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def _chromium() -> str | None:
    import shutil
    for c in CHROMIUM:
        if Path(c).is_file():
            return c
    return shutil.which("chromium") or shutil.which("google-chrome")


def html_to_pdf(html: str, out: Path, *, base_url: str = "") -> bool:
    """Render HTML to PDF so a web source is kept as a fixed page.

    A URL is not an archive -- the page changes or disappears and the note
    becomes a claim with no source. A PDF of what was actually read is.
    Returns False when no browser is installed, and the caller falls back to
    keeping only the Markdown.
    """
    import subprocess
    import tempfile

    chrome = _chromium()
    if chrome is None:
        return False

    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "page.html"
        header = f'<base href="{base_url}">' if base_url else ""
        src.write_text(f"<!doctype html><meta charset=utf-8>{header}{html}",
                       errors="replace")
        out.parent.mkdir(parents=True, exist_ok=True)
        try:
            subprocess.run(
                [chrome, "--headless", "--disable-gpu", "--no-sandbox",
                 "--no-pdf-header-footer", f"--print-to-pdf={out}", src.as_uri()],
                check=True, capture_output=True, timeout=120)
        except (subprocess.SubprocessError, OSError):
            return False
    return out.is_file() and out.stat().st_size > 0


def fetch_url_full(url: str) -> tuple[str, str, str, bytes | None]:
    """A web page as Markdown, plus a PDF snapshot of what was read.

    Returns `(markdown, title, format, pdf_bytes_or_None)`.
    """
    try:
        import trafilatura
    except ImportError:
        raise Unsupported("Web support needs: pip install 'storyos[library]'") from None

    raw = trafilatura.fetch_url(url)
    if not raw:
        raise RuntimeError(f"could not fetch {url}")
    md, title = _html(raw, url)

    pdf = None
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "snapshot.pdf"
        if html_to_pdf(raw, out, base_url=url):
            pdf = out.read_bytes()

    return md, title or urllib.parse.urlparse(url).path.strip("/") or url, "web", pdf
