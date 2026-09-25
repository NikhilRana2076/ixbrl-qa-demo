"""
src/ingestion/text_extraction.py

Plain-text extraction from iXBRL filings for the C1 baseline condition.

Deliberately naive: strips markup to visible text, in document order, with
no awareness of which facts or narratives matter for any given question.
This is what "feed the LLM the raw filing" means in practice once markup
is removed, since raw HTML/XBRL would otherwise waste most of a context
window on tags rather than content.
"""
from __future__ import annotations

import re
from pathlib import Path

from lxml import etree

from .parser import split_tag

WHITESPACE_RE = re.compile(r"\s+")

# Elements whose content is never part of what a reader sees.
STRIP_TAGS = {"script", "style"}


def extract_plain_text(path: Path) -> str:
    """Strip markup from an iXBRL document, returning visible text only.

    ix:header (XBRL metadata block, not part of the rendered document) is
    excluded. Everything else — including ix:continuation content, which
    a browser stitches into the visible flow via JavaScript — is kept,
    since a naive text-extraction pipeline reading the raw file would
    encounter this content regardless of its DOM position.
    """
    parser = etree.HTMLParser(recover=True, huge_tree=True)
    tree = etree.parse(str(path), parser)
    root = tree.getroot()
    if root is None:
        raise ValueError(f"could not parse {path}")

    for tagname in STRIP_TAGS:
        for el in root.iter(tagname):
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    for el in list(root.iter()):
        if not isinstance(el.tag, str):
            continue
        ns, local = split_tag(el.tag)
        if local == "header" and "inlineXBRL" in ns:
            parent = el.getparent()
            if parent is not None:
                parent.remove(el)

    text = " ".join(root.itertext())
    return WHITESPACE_RE.sub(" ", text).strip() 