"""Tiny iXBRL reader: enough of the spec for test fixtures (nonFraction, nonNumeric, contexts)."""
import re
from dataclasses import dataclass, field
from pathlib import Path
from lxml import etree

IX = "http://www.xbrl.org/2013/inlineXBRL"; XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"

@dataclass
class Filing:
    numeric_facts: list = field(default_factory=list)
    narrative_facts: list = field(default_factory=list)

def find_document(p: Path):
    p = Path(p)
    if p.is_file():
        return p
    for c in sorted(p.rglob("*")):
        if c.suffix.lower() in (".xhtml", ".html", ".htm"):
            return c
    return None

def parse_filing(doc):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True)
    root = etree.parse(str(doc), parser).getroot()
    ctx = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        d = {"dims": {}}
        for m in c.iter(f"{{{XBRLDI}}}explicitMember"):
            d["dims"][m.get("dimension")] = m.text.strip()
        s, e, i = (c.find(f".//{{{XBRLI}}}{t}") for t in ("startDate", "endDate", "instant"))
        if i is not None: d.update(ptype="instant", instant=i.text.strip())
        else: d.update(ptype="duration", start=s.text.strip(), end=e.text.strip())
        ctx[c.get("id")] = d
    f = Filing()
    for n in root.iter(f"{{{IX}}}nonFraction"):
        c = ctx[n.get("contextRef")]; raw = "".join(n.itertext())
        v = float(re.sub(r"[^\d.]", "", raw) or 0) * 10 ** int(n.get("scale", "0"))
        if n.get("sign") == "-": v = -v
        name = n.get("name"); pre = name.split(":")[0]
        fam = {"ifrs-full": "ifrs", "core": "frs"}.get(pre, "extension")
        unit = n.get("unitRef", "").upper().replace("U-", "")
        f.numeric_facts.append(dict(concept=name, value=v, raw=raw, scale=int(n.get("scale", "0")), unit=unit,
            family=fam, ctx=n.get("contextRef"), ptype=c["ptype"], start=c.get("start"), end=c.get("end"),
            instant=c.get("instant"), dims=c["dims"]))
    for n in root.iter(f"{{{IX}}}nonNumeric"):
        c = ctx[n.get("contextRef")]
        f.narrative_facts.append(dict(concept=n.get("name"), text=" ".join("".join(n.itertext()).split()),
            ctx=n.get("contextRef"), start=c.get("start"), end=c.get("end")))
    return f
