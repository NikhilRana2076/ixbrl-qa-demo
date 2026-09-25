"""
src/ingestion/parser.py

Direct lxml-based iXBRL parser for UK statutory filings.

Deliberately does not use Arelle: Arelle resolves taxonomy schemas over the
network at runtime, which is a reproducibility hazard. This module reads only
the inline facts present in the document itself.

Extracts:
  - contexts  (period + dimensional qualifiers)
  - units     (currency / shares / pure)
  - numeric facts    (ix:nonFraction) with scale and sign applied
  - narrative facts  (ix:nonNumeric) with continuation chains resolved
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

# --- Namespaces -------------------------------------------------------------

IX_NAMESPACES = {
    "http://www.xbrl.org/2013/inlineXBRL",   # iXBRL 1.1
    "http://www.xbrl.org/2008/inlineXBRL",   # iXBRL 1.0
}
XBRLI_NS = "http://www.xbrl.org/2003/instance"
XBRLDI_NS = "http://xbrl.org/2006/xbrldi"

LINK_NS = "http://www.xbrl.org/2003/linkbase"
XLINK_NS = "http://www.w3.org/1999/xlink"
# Namespace fragments belonging to published (non-company-specific) taxonomies.
# Anything outside these is treated as a company extension concept.
STANDARD_TAXONOMY_MARKERS = (
    "xbrl.ifrs.org",
    "xbrl.frc.org.uk",
    "companieshouse.gov.uk",
    "xbrl.org",
    "w3.org",
)

# --- Value cleaning ---------------------------------------------------------

# Characters filings use to mean "nil". Includes several dash variants.
NIL_TOKENS = {"", "-", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014",
              "\u2015", "\u2212", "nil", "n/a", "na"}

# Zero-width and exotic spaces that Python's \s does not always catch.
INVISIBLE_CHARS = ("\u200b", "\u200c", "\u200d", "\ufeff", "\u00ad")

WHITESPACE_RE = re.compile(r"\s+")


def clean_number_text(raw: str) -> str:
    """Strip formatting from a displayed number.

    Filings split digits across elements and lines, producing values like
    '32 ,812' or '3 2,238'. Every whitespace character must go, not just
    the leading and trailing ones.
    """
    text = raw
    for ch in INVISIBLE_CHARS:
        text = text.replace(ch, "")
    text = WHITESPACE_RE.sub("", text)
    text = text.replace(",", "").replace("\u00a0", "")
    return text

def split_tag(tag) -> tuple[str, str]:
    """Split '{namespace}localname' into (namespace, localname)."""
    if isinstance(tag, str) and tag.startswith("{"):
        uri, local = tag[1:].split("}", 1)
        return uri, local
    return "", str(tag)

def element_text(el) -> str:
    """All descendant text, excluding any ix:exclude subtrees.

    ix:exclude marks content inside a tagged fact that is not part of the
    fact's value (footnote markers, annotations). Including it would corrupt
    narrative text and, for numerics, splice in stray characters.
    """
    parts = []

    def walk(node, top=False):
        if not top and isinstance(node.tag, str):
            ns, local = split_tag(node.tag)
            if ns in IX_NAMESPACES and local == "exclude":
                if node.tail:
                    parts.append(node.tail)
                return
        if node.text:
            parts.append(node.text)
        for child in node:
            walk(child)
        if not top and node.tail:
            parts.append(node.tail)

    walk(el, top=True)
    return "".join(parts)

def parse_schema_refs(root) -> list[str]:
    """Taxonomy schema references. These state definitively which reporting
    framework the filing was prepared under, unlike narrative text."""
    refs = []
    for el in root.iter(f"{{{LINK_NS}}}schemaRef"):
        href = el.get(f"{{{XLINK_NS}}}href")
        if href:
            refs.append(href)
    return refs

def resolve_qname(el, qname: str) -> tuple[str, str, str]:
    """Resolve 'prefix:local' against the element's namespace map.

    Returns (prefix, local_name, namespace_uri).
    """
    if ":" in qname:
        prefix, local = qname.split(":", 1)
    else:
        prefix, local = "", qname
    uri = el.nsmap.get(prefix or None, "") or ""
    return prefix, local, uri


def is_extension_concept(namespace_uri: str) -> bool:
    """True if the concept comes from a company-specific extension taxonomy."""
    if not namespace_uri:
        return False
    return not any(m in namespace_uri for m in STANDARD_TAXONOMY_MARKERS)


# --- Data structures --------------------------------------------------------

@dataclass
class Context:
    id: str
    entity: str | None = None
    period_type: str = "unknown"          # "instant" | "duration"
    start_date: str | None = None
    end_date: str | None = None
    instant: str | None = None
    dimensions: dict[str, str] = field(default_factory=dict)

    @property
    def has_dimension(self) -> bool:
        return bool(self.dimensions)

    def period_label(self) -> str:
        if self.period_type == "instant":
            return f"as at {self.instant}"
        if self.period_type == "duration":
            return f"{self.start_date} to {self.end_date}"
        return "unknown period"


@dataclass
class Unit:
    id: str
    measures: list[str] = field(default_factory=list)
    divide_numerator: list[str] = field(default_factory=list)
    divide_denominator: list[str] = field(default_factory=list)

    def label(self) -> str:
        if self.divide_numerator:
            num = "+".join(self.divide_numerator)
            den = "+".join(self.divide_denominator)
            return f"{num}/{den}"
        return "+".join(self.measures) if self.measures else "unknown"


@dataclass
class NumericFact:
    concept: str                 # 'ifrs-full:Revenue' as written in the filing
    local_name: str
    namespace: str
    is_extension: bool
    raw_text: str                # exactly as displayed, for evidence citation
    value: float | None          # scale and sign applied
    is_nil: bool
    scale: int
    decimals: str | None
    sign: str | None
    format: str | None
    context_id: str
    unit_id: str
    parse_error: str | None = None


@dataclass
class NarrativeFact:
    concept: str
    local_name: str
    namespace: str
    is_extension: bool
    text: str
    context_id: str
    continuation_count: int = 0


@dataclass
class ParsedFiling:
    source_path: str
    contexts: dict[str, Context]
    units: dict[str, Unit]
    numeric_facts: list[NumericFact]
    narrative_facts: list[NarrativeFact]
    schema_refs: list[str] = field(default_factory=list)

# --- Parsing ----------------------------------------------------------------

def load_document(path: Path):
    """Parse the iXBRL document. no_network prevents any DTD/entity fetch."""
    parser = etree.XMLParser(
        recover=True, huge_tree=True, resolve_entities=False,
        load_dtd=False, no_network=True,
    )
    tree = etree.parse(str(path), parser)
    return tree.getroot()


def parse_contexts(root) -> dict[str, Context]:
    contexts: dict[str, Context] = {}
    for el in root.iter(f"{{{XBRLI_NS}}}context"):
        ctx = Context(id=el.get("id", ""))

        ident = el.find(f".//{{{XBRLI_NS}}}identifier")
        if ident is not None and ident.text:
            ctx.entity = ident.text.strip()

        instant = el.find(f".//{{{XBRLI_NS}}}instant")
        start = el.find(f".//{{{XBRLI_NS}}}startDate")
        end = el.find(f".//{{{XBRLI_NS}}}endDate")
        if instant is not None and instant.text:
            ctx.period_type = "instant"
            ctx.instant = instant.text.strip()
        elif start is not None and end is not None:
            ctx.period_type = "duration"
            ctx.start_date = (start.text or "").strip()
            ctx.end_date = (end.text or "").strip()

        for member in el.iter(f"{{{XBRLDI_NS}}}explicitMember"):
            dim = member.get("dimension", "")
            val = (member.text or "").strip()
            if dim:
                ctx.dimensions[dim] = val
        for member in el.iter(f"{{{XBRLDI_NS}}}typedMember"):
            dim = member.get("dimension", "")
            if dim:
                ctx.dimensions[dim] = element_text(member).strip()

        contexts[ctx.id] = ctx
    return contexts


def parse_units(root) -> dict[str, Unit]:
    units: dict[str, Unit] = {}
    for el in root.iter(f"{{{XBRLI_NS}}}unit"):
        unit = Unit(id=el.get("id", ""))
        divide = el.find(f"{{{XBRLI_NS}}}divide")
        if divide is not None:
            num = divide.find(f"{{{XBRLI_NS}}}unitNumerator")
            den = divide.find(f"{{{XBRLI_NS}}}unitDenominator")
            if num is not None:
                unit.divide_numerator = [
                    (m.text or "").strip()
                    for m in num.iter(f"{{{XBRLI_NS}}}measure")
                ]
            if den is not None:
                unit.divide_denominator = [
                    (m.text or "").strip()
                    for m in den.iter(f"{{{XBRLI_NS}}}measure")
                ]
        else:
            unit.measures = [
                (m.text or "").strip()
                for m in el.iter(f"{{{XBRLI_NS}}}measure")
            ]
        units[unit.id] = unit
    return units


def parse_numeric_fact(el) -> NumericFact:
    concept = el.get("name", "")
    _prefix, local, uri = resolve_qname(el, concept)

    raw = element_text(el)
    cleaned = clean_number_text(raw)

    try:
        scale = int(el.get("scale", "0") or "0")
    except ValueError:
        scale = 0

    fact = NumericFact(
        concept=concept,
        local_name=local,
        namespace=uri,
        is_extension=is_extension_concept(uri),
        raw_text=raw.strip(),
        value=None,
        is_nil=False,
        scale=scale,
        decimals=el.get("decimals"),
        sign=el.get("sign"),
        format=el.get("format"),
        context_id=el.get("contextRef", ""),
        unit_id=el.get("unitRef", ""),
    )

    # Explicit XML nil
    if el.get(f"{{http://www.w3.org/2001/XMLSchema-instance}}nil") == "true":
        fact.is_nil = True
        fact.value = 0.0
        return fact

    # Dash characters and similar mean nil in printed accounts
    if cleaned.lower() in NIL_TOKENS:
        fact.is_nil = True
        fact.value = 0.0
        return fact

    negative_from_brackets = cleaned.startswith("(") and cleaned.endswith(")")
    if negative_from_brackets:
        cleaned = cleaned[1:-1]

    # Keep digits, decimal point and a leading minus only
    cleaned = re.sub(r"[^\d.\-]", "", cleaned)

    try:
        value = float(cleaned)
    except ValueError:
        fact.parse_error = f"could not convert {raw.strip()!r}"
        return fact

    value *= 10 ** scale

    # The sign attribute is authoritative; brackets are a display fallback
    # used when no sign attribute is present.
    if fact.sign == "-":
        value = -abs(value)
    elif negative_from_brackets:
        value = -abs(value)

    fact.value = value
    return fact


def build_continuation_map(root) -> dict[str, object]:
    cont: dict[str, object] = {}
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        ns, local = split_tag(el.tag)
        if ns in IX_NAMESPACES and local == "continuation":
            cid = el.get("id")
            if cid:
                cont[cid] = el
    return cont


def resolve_continuations(el, cont_map: dict[str, object]) -> tuple[str, int]:
    """Follow the continuedAt chain, concatenating text. Returns (text, hops)."""
    parts = [element_text(el)]
    hops = 0
    seen: set[str] = set()
    next_id = el.get("continuedAt")
    while next_id and next_id not in seen and next_id in cont_map:
        seen.add(next_id)
        nxt = cont_map[next_id]
        parts.append(element_text(nxt))
        hops += 1
        next_id = nxt.get("continuedAt")
    text = " ".join(parts)
    return WHITESPACE_RE.sub(" ", text).strip(), hops


def parse_filing(path: Path) -> ParsedFiling:
    root = load_document(path)

    contexts = parse_contexts(root)
    units = parse_units(root)
    cont_map = build_continuation_map(root)

    numeric: list[NumericFact] = []
    narrative: list[NarrativeFact] = []

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        ns, local = split_tag(el.tag)
        if ns not in IX_NAMESPACES:
            continue

        if local == "nonFraction":
            numeric.append(parse_numeric_fact(el))

        elif local == "nonNumeric":
            concept = el.get("name", "")
            _prefix, lname, uri = resolve_qname(el, concept)
            text, hops = resolve_continuations(el, cont_map)
            narrative.append(NarrativeFact(
                concept=concept,
                local_name=lname,
                namespace=uri,
                is_extension=is_extension_concept(uri),
                text=text,
                context_id=el.get("contextRef", ""),
                continuation_count=hops,
            ))

    return ParsedFiling(
        source_path=str(path),
        contexts=contexts,
        units=units,
        numeric_facts=numeric,
        narrative_facts=narrative,
        schema_refs=parse_schema_refs(root),       
    )

DOC_SUFFIXES = {".xhtml", ".html", ".htm"}


def find_document(target: Path) -> Path | None:
    """Locate the iXBRL document in a filing directory.

    Handles XBRL Report Packages (document under reports/) and loose files.
    Returns None if nothing suitable is found.
    """
    if target.is_file():
        return target
    if not target.is_dir():
        return None

    files = sorted(
        (p for p in target.rglob("*") if p.is_file()),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    in_reports = [p for p in files if "reports" in {q.lower() for q in p.parts}]
    if in_reports:
        return in_reports[0]
    candidates = [p for p in files if p.suffix.lower() in DOC_SUFFIXES]
    if candidates:
        return candidates[0]
    return None