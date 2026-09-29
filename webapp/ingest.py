"""
Hardened upload -> parse -> fact store.

Reuses the dissertation's own ingestion modules unchanged
(src.ingestion.parser / src.ingestion.store). What is added here is only
the defensive wrapping a public upload endpoint needs:
  * filename sanitised (no path traversal via "../../app.py")
  * extension allow-list and a content sniff
  * zip bomb / zip-slip protection (member count, total size, safe paths)
  * a semaphore so only N filings are parsed at once (lxml on a 25 MB
    filing can use a few hundred MB of RAM; the free tier has little)
  * the raw upload is deleted as soon as parsing finishes
"""
from __future__ import annotations

import shutil
import threading
import zipfile
from pathlib import Path, PurePosixPath

from werkzeug.utils import secure_filename

ALLOWED_EXT = {".xhtml", ".html", ".htm", ".xml", ".zip"}
COMPANY_ID = "DEMO"


class UploadError(Exception):
    """Message is safe to show to the visitor."""


_parse_gate: threading.BoundedSemaphore | None = None


def init_gate(n: int) -> None:
    global _parse_gate
    _parse_gate = threading.BoundedSemaphore(max(1, n))


def _safe_extract(zip_path: Path, dest: Path, max_members: int, max_bytes: int) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > max_members:
            raise UploadError("This zip contains too many files.")
        if sum(i.file_size for i in infos) > max_bytes:
            raise UploadError("This zip expands to more than the size limit.")
        for info in infos:
            p = PurePosixPath(info.filename.replace("\\", "/"))
            if p.is_absolute() or ".." in p.parts:
                continue                                    # zip-slip: skip
            target = dest.joinpath(*p.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with zf.open(info) as src, open(target, "wb") as out:
                while chunk := src.read(1 << 20):
                    written += len(chunk)
                    if written > info.file_size + 1024:     # lying header
                        raise UploadError("Corrupt zip file.")
                    out.write(chunk)


def _sniff(path: Path) -> None:
    head = path.open("rb").read(4096)
    if path.suffix.lower() == ".zip":
        if not head.startswith(b"PK"):
            raise UploadError("That file is not a valid zip archive.")
        return
    low = head.lower()
    if b"<" not in low or b"<script" in low[:200]:
        raise UploadError("That file does not look like an iXBRL (XHTML) document.")


def ingest_upload(file_storage, workdir: Path, settings) -> tuple[Path, dict]:
    """Save, validate, parse and load a filing. Returns (db_path, counts)."""
    name = secure_filename(file_storage.filename or "") or "filing.xhtml"
    if Path(name).suffix.lower() not in ALLOWED_EXT:
        raise UploadError("Please upload an iXBRL filing (.xhtml, .html) or a .zip report package.")

    incoming = workdir / "incoming"
    incoming.mkdir()
    raw = incoming / name
    file_storage.save(raw)
    return ingest_path(raw, workdir, settings, display_name=name)


def ingest_path(raw: Path, workdir: Path, settings, display_name: str) -> tuple[Path, dict]:
    from src.ingestion.parser import find_document, parse_filing
    from src.ingestion.store import connect, insert_company, load_filing

    incoming = raw.parent
    try:
        _sniff(raw)
        target = raw
        if raw.suffix.lower() == ".zip":
            target = incoming / "unzipped"
            target.mkdir()
            _safe_extract(raw, target, settings.max_zip_members,
                          settings.max_unzipped_mb * 1024 * 1024)
            raw.unlink(missing_ok=True)

        doc = find_document(target)
        if doc is None:
            raise UploadError("No iXBRL document was found in this file.")

        assert _parse_gate is not None
        if not _parse_gate.acquire(timeout=60):
            raise UploadError("The server is busy parsing another filing. Please try again in a minute.")
        try:
            filing = parse_filing(doc)
        finally:
            _parse_gate.release()

        db_path = workdir / "facts.sqlite"
        conn = connect(str(db_path))
        try:
            insert_company(conn, {
                "company_number": COMPANY_ID,
                "company_name": display_name,
                "numeric_facts": len(filing.numeric_facts),
                "narrative_facts": len(filing.narrative_facts),
            })
            load_filing(conn, COMPANY_ID, filing)
            conn.commit()
        finally:
            conn.close()
        counts = {"n_facts": len(filing.numeric_facts),
                  "n_narratives": len(filing.narrative_facts)}
        del filing
        return db_path, counts
    except UploadError:
        raise
    except (zipfile.BadZipFile, UnicodeDecodeError):
        raise UploadError("The file could not be read. Is it a complete iXBRL filing?") from None
    except Exception as exc:                                  # noqa: BLE001
        # Log the detail server-side; show the visitor a generic message.
        import logging
        logging.getLogger(__name__).exception("parse failure: %s", exc)
        raise UploadError("This filing could not be parsed. Try the original .xhtml or the report package .zip.") from exc
    finally:
        shutil.rmtree(incoming, ignore_errors=True)   # never keep the raw upload
