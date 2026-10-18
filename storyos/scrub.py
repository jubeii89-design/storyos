"""Strip what the writer did not choose to say.

An exported manuscript carries more than the words. A PDF announces which
renderer made it; a DOCX claims to be Microsoft Word 12 and records the minute
you built it; an EPUB stamps a timestamp and a fresh UUID on every export. None
of that is in the book, and a manuscript going to an agent should not volunteer
the hour you finished it or which tools you used to get there.

The line this module draws: **title and author stay, because the writer chose
them. Everything about the machine, the moment and the software goes.**

It is not a security boundary -- a determined reader can tell a lot from a PDF
regardless. It is the difference between a file that says what you meant it to
say and one that also says things you never decided to publish.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from pathlib import Path

PRODUCER = "Story OS"
# A placeholder that is obviously one, used where a format demands a
# timestamp it has no business learning.
EPOCH = "1980-01-01T00:00:00Z"

# Timestamps, tool names, and the per-build identifiers that make two exports
# of the same manuscript differ for no reason the reader cares about.
DOCX_DROP = (
    "Application", "AppVersion", "Template", "TotalTime", "Company",
    "Manager", "LastModifiedBy", "lastModifiedBy", "revision", "Revision",
    "DocSecurity", "ScaleCrop", "LinksUpToDate", "SharedDoc",
    "HyperlinksChanged",
)


def pdf(path: Path, *, title: str = "", author: str = "") -> dict:
    """Rewrite a PDF's document information dictionary.

    weasyprint writes `/Producer: WeasyPrint 70.0` and Chromium writes
    `/Producer: Skia/PDF`. Both are accurate and neither is the writer's
    business to disclose.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return {"ok": False, "why": "pypdf not installed"}

    reader = PdfReader(str(path))
    before = dict(reader.metadata or {})

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    meta = {"/Producer": PRODUCER, "/Creator": PRODUCER}
    if title:
        meta["/Title"] = title
    if author:
        meta["/Author"] = author
    writer.add_metadata(meta)

    tmp = path.with_suffix(path.suffix + ".clean")
    with tmp.open("wb") as fh:
        writer.write(fh)
    tmp.replace(path)

    after = dict(PdfReader(str(path)).metadata or {})
    return {"ok": True, "removed": sorted(set(before) - set(after)),
            "kept": sorted(after)}


def _rewrite_zip(path: Path, fix) -> list[str]:
    """Rebuild a zip, passing each member through `fix(name, data)`.

    Rewriting in place is not possible for zip containers, and copying through
    a temporary file means a failure leaves the original export intact rather
    than a half-written one.
    """
    tmp = path.with_suffix(path.suffix + ".clean")
    touched: list[str] = []
    with zipfile.ZipFile(path) as src, \
         zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            new = fix(item.filename, data)
            if new is not None and new != data:
                touched.append(item.filename)
                data = new
            # A fixed date stamp, so two exports of one manuscript are
            # byte-comparable instead of differing by the clock.
            info = zipfile.ZipInfo(item.filename, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = item.external_attr
            dst.writestr(info, data)
    tmp.replace(path)
    return touched


def docx(path: Path, *, title: str = "", author: str = "") -> dict:
    """Clean an OOXML package.

    pandoc writes `Application: Microsoft Word 12.0.0`, which is not even true,
    and `dcterms:created`/`modified` to the second.
    """
    def fix(name: str, data: bytes) -> bytes | None:
        if not name.startswith("docProps/"):
            return None
        xml = data.decode("utf-8", errors="replace")
        for tag in DOCX_DROP:
            xml = re.sub(rf"<(\w+:)?{tag}\b[^>]*>.*?</(\w+:)?{tag}>", "", xml,
                         flags=re.DOTALL)
            xml = re.sub(rf"<(\w+:)?{tag}\b[^>]*/>", "", xml)
        xml = re.sub(r"<dcterms:(created|modified)\b[^>]*>.*?</dcterms:\1>", "",
                     xml, flags=re.DOTALL)
        if title:
            xml = re.sub(r"<dc:title>.*?</dc:title>", f"<dc:title>{title}</dc:title>",
                         xml, flags=re.DOTALL)
        if author:
            xml = re.sub(r"<dc:creator>.*?</dc:creator>",
                         f"<dc:creator>{author}</dc:creator>", xml, flags=re.DOTALL)
        return xml.encode("utf-8")

    touched = _rewrite_zip(path, fix)
    return {"ok": True, "rewrote": touched}


def epub(path: Path, *, title: str = "", author: str = "",
         identifier: str = "") -> dict:
    """Clean an EPUB package document.

    The build timestamp goes. The identifier becomes stable and derived from
    the title rather than random, so re-exporting the same book does not
    produce a different work as far as a reader's library is concerned.
    """
    ident = identifier or ("urn:storyos:" + re.sub(r"[^a-z0-9]+", "-",
                                                   (title or path.stem).lower()).strip("-"))

    def fix(name: str, data: bytes) -> bytes | None:
        # The NCX carries dtb:uid, which the spec requires to match the
        # package's unique identifier. pandoc writes a fresh random UUID there
        # on every build, so once the OPF identifier is made stable the two
        # disagree -- an actual conformance error, not only a reproducibility
        # one. Rewriting it fixes both.
        if name.endswith(".ncx"):
            xml = data.decode("utf-8", errors="replace")
            xml = re.sub(r'(<meta\s+name="dtb:uid"\s+content=")[^"]*(")',
                         rf"\g<1>{ident}\g<2>", xml)
            return xml.encode("utf-8")
        if not name.endswith(".opf"):
            return None
        xml = data.decode("utf-8", errors="replace")
        # Normalised, not removed. EPUB 3 requires dcterms:modified, so
        # deleting it produces a file that fails validation -- and an invalid
        # manuscript is a worse outcome than a disclosed build time. A fixed
        # stamp keeps the format happy and carries no information.
        xml = re.sub(r"(<dc:date[^>]*>).*?(</dc:date>)", rf"\g<1>{EPOCH}\g<2>",
                     xml, flags=re.DOTALL)
        xml = re.sub(r'(<meta[^>]*property="dcterms:modified"[^>]*>).*?(</meta>)',
                     rf"\g<1>{EPOCH}\g<2>", xml, flags=re.DOTALL)
        xml = re.sub(r"<dc:identifier([^>]*)>.*?</dc:identifier>",
                     rf"<dc:identifier\1>{ident}</dc:identifier>", xml,
                     flags=re.DOTALL)
        if title:
            xml = re.sub(r"<dc:title([^>]*)>.*?</dc:title>",
                         rf"<dc:title\1>{title}</dc:title>", xml, flags=re.DOTALL)
        if author:
            xml = re.sub(r"<dc:creator([^>]*)>.*?</dc:creator>",
                         rf"<dc:creator\1>{author}</dc:creator>", xml, flags=re.DOTALL)
        return xml.encode("utf-8")

    touched = _rewrite_zip(path, fix)
    return {"ok": True, "rewrote": touched, "identifier": ident}


def clean(path: Path, *, title: str = "", author: str = "") -> dict:
    """Clean whichever kind of file this is."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return pdf(path, title=title, author=author)
    if suffix == ".docx":
        return docx(path, title=title, author=author)
    if suffix == ".epub":
        return epub(path, title=title, author=author)
    return {"ok": False, "why": f"nothing to clean in {suffix}"}


def report(path: Path) -> dict:
    """What a file currently discloses. Read-only, for showing the writer."""
    suffix = path.suffix.lower()
    out: dict = {"file": path.name}

    if suffix == ".pdf":
        try:
            from pypdf import PdfReader
            out["metadata"] = {str(k): str(v) for k, v in
                               (PdfReader(str(path)).metadata or {}).items()}
        except ImportError:
            out["metadata"] = {}
        return out

    if suffix in (".docx", ".epub"):
        found: dict[str, str] = {}
        with zipfile.ZipFile(path) as z:
            for name in z.namelist():
                if not (name.startswith("docProps/") or name.endswith(".opf")):
                    continue
                xml = z.read(name).decode("utf-8", errors="replace")
                for m in re.finditer(r"<([a-zA-Z:]+)[^>]*>([^<]{1,80})</\1>", xml):
                    found[m.group(1)] = m.group(2)
        out["metadata"] = found
    return out
