"""Export the manuscript. No model, and no hand-styling afterwards.

DOCX goes through pandoc with `--reference-doc`, so the file arrives in standard
submission shape -- double-spaced, indented first lines, a title page, page
numbers -- rather than needing an afternoon in Word. EPUB is the same pipeline.

PDF goes through headless Chromium rather than a LaTeX engine, because the
cover and the interior should be rendered by the same thing that renders the
cover, and because no TeX distribution is required.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .commit import rebuild_manuscript

CHROMIUM = (
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)

PAGE_CSS = """
@page { size: 6in 9in; margin: 0.75in 0.6in; }
html { font-size: 11.5pt; }
body { font-family: "Newsreader", Georgia, "Times New Roman", serif;
       line-height: 1.55; text-align: justify; hyphens: auto; margin: 0; }
h1 { font-size: 1.05rem; font-weight: 500; letter-spacing: 0.14em;
     text-transform: uppercase; text-align: center; margin: 0 0 2.2em;
     page-break-before: always; }
h1:first-of-type { page-break-before: avoid; }
p { margin: 0; text-indent: 1.4em; }
/* The first paragraph after a chapter heading is not indented -- the heading
   already marks the start, and an indent there reads as an error in print. */
h1 + p { text-indent: 0; }
p + p { margin-top: 0; }
.title-page { page-break-after: always; text-align: center; padding-top: 3in; }
.title-page h2 { font-size: 1.8rem; font-weight: 500; margin: 0 0 0.6em; }
.title-page .by { font-size: 1rem; color: #444; }
"""


def _find_chromium() -> str | None:
    for p in CHROMIUM:
        if Path(p).is_file():
            return p
    return shutil.which("chromium") or shutil.which("google-chrome")


def _pandoc():
    try:
        import pypandoc
        return pypandoc
    except ImportError:
        raise RuntimeError(
            "pandoc is not installed. `pip install pypandoc-binary` -- it bundles "
            "the binary, so no system package is needed."
        ) from None


def run(env, *, formats: tuple[str, ...] = ("docx", "epub", "pdf")) -> dict:
    """Rebuild the manuscript from committed chapters, then convert it."""
    stats = rebuild_manuscript(env)
    if not stats["chapters"]:
        raise FileNotFoundError(
            "no committed chapters to export. `chapter_commit` writes the "
            "chapters this reads.")

    env.export_dir.mkdir(parents=True, exist_ok=True)
    title = env.title
    author = env.config.get("author", "")
    out: dict[str, str] = {}

    md = env.manuscript.read_text()
    front = f"---\ntitle: {json.dumps(title)}\n" + \
            (f"author: {json.dumps(author)}\n" if author else "") + "---\n\n"
    staged = env.export_dir / "manuscript.staged.md"
    staged.write_text(front + md)

    for fmt in formats:
        if fmt == "pdf":
            path = _pdf(env, md, title, author)
        else:
            pypandoc = _pandoc()
            path = env.export_dir / f"manuscript.{fmt}"
            extra = ["--toc", "--toc-depth=1", "--standalone"]
            ref = env.docx_reference
            if fmt == "docx" and ref.is_file():
                extra.append(f"--reference-doc={ref}")
            pypandoc.convert_file(str(staged), fmt, outputfile=str(path),
                                  extra_args=extra)
        if path:
            out[fmt] = str(path)

    staged.unlink(missing_ok=True)
    return {"chapters": stats["chapters"], "words": stats["words"], "files": out}


def _pdf(env, md: str, title: str, author: str) -> Path | None:
    """Markdown to print-ready PDF through headless Chromium."""
    chrome = _find_chromium()
    if chrome is None:
        return None

    body = _md_to_html(md)
    byline = f'<div class="by">{author}</div>' if author else ""
    html = (f"<!doctype html><meta charset=utf-8><title>{title}</title>"
            f"<style>{PAGE_CSS}</style>"
            f'<div class="title-page"><h2>{title}</h2>{byline}</div>{body}')

    src = env.export_dir / "manuscript.print.html"
    src.write_text(html)
    out = env.export_dir / "manuscript.pdf"

    subprocess.run(
        [chrome, "--headless", "--disable-gpu", "--no-sandbox",
         "--no-pdf-header-footer", f"--print-to-pdf={out}", src.as_uri()],
        check=True, capture_output=True, timeout=180,
    )
    src.unlink(missing_ok=True)
    return out if out.is_file() else None


def _md_to_html(md: str) -> str:
    """Just enough: headings and paragraphs. The manuscript has no other markup."""
    html: list[str] = []
    for block in md.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("# "):
            html.append(f"<h1>{_esc(block[2:].strip())}</h1>")
        else:
            html.append(f"<p>{_esc(block)}</p>")
    return "".join(html)


def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
             .replace("\n", " "))
