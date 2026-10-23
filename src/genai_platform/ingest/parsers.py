"""Layout-aware parsing into ``Document`` objects.

Local parsers handle Markdown, text, HTML, PDF and DOCX. Cloud parsers (Amazon Textract,
Azure AI Document Intelligence) handle scanned PDFs and complex tables and return Markdown so
the chunkers can follow headings and keep tables whole.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import yaml

from ..types import Document

_FRONT = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _doc_id(path: Path) -> str:
    return hashlib.sha1(str(path).encode()).hexdigest()[:16]


def _front_matter(text: str) -> tuple[dict, str]:
    m = _FRONT.match(text)
    if not m:
        return {}, text
    return yaml.safe_load(m.group(1)) or {}, text[m.end():]


def parse_markdown(path: Path) -> Document:
    meta, body = _front_matter(path.read_text(encoding="utf-8"))
    title = meta.pop("title", None) or next(
        (ln.lstrip("# ").strip() for ln in body.splitlines() if ln.startswith("# ")), path.stem)
    return Document(id=_doc_id(path), text=body, source=str(path), title=title, doc_type="markdown",
                    acl=meta.pop("acl", ["public"]), metadata=meta)


def parse_html(path: Path) -> Document:
    raw = path.read_text(encoding="utf-8")
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw, "html.parser")
        for h in soup.find_all(re.compile("^h[1-6]$")):
            h.replace_with(f"\n{'#' * int(h.name[1])} {h.get_text(strip=True)}\n")
        text = soup.get_text("\n")
    except ImportError:
        text = re.sub(r"<[^>]+>", " ", raw)
    return Document(id=_doc_id(path), text=text, source=str(path), title=path.stem, doc_type="html")


def parse_pdf(path: Path) -> Document:
    from pypdf import PdfReader

    pages = [p.extract_text() or "" for p in PdfReader(str(path)).pages]
    text = "\n\n".join(f"## Page {i + 1}\n{t}" for i, t in enumerate(pages))
    return Document(id=_doc_id(path), text=text, source=str(path), title=path.stem, doc_type="pdf")


def parse_docx(path: Path) -> Document:
    import docx

    lines = []
    for p in docx.Document(str(path)).paragraphs:
        style = (p.style.name or "").lower()
        if style.startswith("heading"):
            level = int(style.split()[-1]) if style.split()[-1].isdigit() else 2
            lines.append(f"{'#' * level} {p.text}")
        else:
            lines.append(p.text)
    return Document(id=_doc_id(path), text="\n".join(lines), source=str(path), title=path.stem, doc_type="docx")


PARSERS = {".md": parse_markdown, ".txt": parse_markdown, ".html": parse_html, ".htm": parse_html,
           ".pdf": parse_pdf, ".docx": parse_docx}


def parse_path(path: str | Path) -> list[Document]:
    p = Path(path)
    files = sorted(p.rglob("*")) if p.is_dir() else [p]
    return [PARSERS[f.suffix.lower()](f) for f in files if f.suffix.lower() in PARSERS]


class TextractParser:
    """Amazon Textract (tables + layout) -> Markdown. Use for scanned or form-heavy PDFs in S3."""

    def __init__(self, region: str | None = None):
        import boto3

        self.tx = boto3.client("textract", region_name=region)

    def parse_s3(self, bucket: str, key: str) -> Document:
        r = self.tx.analyze_document(Document={"S3Object": {"Bucket": bucket, "Name": key}},
                                     FeatureTypes=["TABLES", "LAYOUT"])
        lines = []
        for b in r["Blocks"]:
            if b["BlockType"] == "LAYOUT_TITLE":
                lines.append("# ")
            elif b["BlockType"] == "LAYOUT_SECTION_HEADER":
                lines.append("## ")
            elif b["BlockType"] == "LINE":
                lines.append(b["Text"])
        text = "\n".join(lines).replace("# \n", "# ").replace("## \n", "## ")
        return Document(id=hashlib.sha1(f"{bucket}/{key}".encode()).hexdigest()[:16], text=text,
                        source=f"s3://{bucket}/{key}", title=key.rsplit("/", 1)[-1], doc_type="pdf")


class DocumentIntelligenceParser:
    """Azure AI Document Intelligence ``prebuilt-layout`` with Markdown output."""

    def __init__(self, endpoint: str):
        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.identity import DefaultAzureCredential

        self.c = DocumentIntelligenceClient(endpoint, DefaultAzureCredential())

    def parse_url(self, url: str) -> Document:
        poller = self.c.begin_analyze_document("prebuilt-layout", {"urlSource": url}, output_content_format="markdown")
        text = poller.result().content
        return Document(id=hashlib.sha1(url.encode()).hexdigest()[:16], text=text, source=url,
                        title=url.rsplit("/", 1)[-1], doc_type="pdf")
