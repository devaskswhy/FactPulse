"""Ingest every PDF in a folder, one at a time, and show the layer growing.

Uses the same `ingest_pdf()` the API route calls -- not a parallel
implementation. The HTTP layer only reads the upload and maps exceptions to
status codes; everything below that is shared, so what this script exercises is
exactly what an upload exercises.

    python scripts/bulk_ingest.py ../samples/starter-datasets/delhivery
    python scripts/bulk_ingest.py <folder> --pages 1-20 --no-link
    python scripts/bulk_ingest.py <folder> --dry-run

The per-document timing breakdown is the point: it shows that ingesting the
Nth document does not re-touch the first N-1.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Run from anywhere: put /backend on the path before importing the app.
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import settings  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.database import get_conn, init_db  # noqa: E402
from app.services.ingest import ingest_pdf  # noqa: E402
from app.services.pdf import PdfParseError  # noqa: E402


def human(seconds: float) -> str:
    return f"{seconds:6.1f}s" if seconds >= 1 else f"{seconds * 1000:5.0f}ms"


def snapshot(conn) -> dict[str, int]:
    return repo.knowledge_layer_totals(conn)


def print_layer(totals: dict[str, int], prefix: str = "") -> None:
    print(
        f"{prefix}documents={totals['documents']}  facts={totals['facts']}  "
        f"types={totals['fact_types']}  embeddings={totals['embeddings']}  "
        f"relationships={totals['relationships']} "
        f"(cross-doc {totals['cross_document_relationships']})  "
        f"open-review={totals['open_review_items']}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Folder of PDFs to ingest.")
    parser.add_argument(
        "--pages",
        help="Limit each PDF to a page range, e.g. 1-20. Keeps model cost down.",
    )
    parser.add_argument(
        "--no-extract", action="store_true", help="Ingest and chunk only."
    )
    parser.add_argument(
        "--no-link", action="store_true", help="Extract but skip relationship linking."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be ingested, with chunk estimates, and stop.",
    )
    args = parser.parse_args()

    folder = args.folder if args.folder.is_absolute() else (Path.cwd() / args.folder)
    folder = folder.resolve()
    if not folder.is_dir():
        print(f"not a folder: {folder}", file=sys.stderr)
        return 2

    pdfs = sorted(folder.rglob("*.pdf"))
    if not pdfs:
        print(f"no PDFs under {folder}", file=sys.stderr)
        return 2

    if args.no_link:
        settings.link_on_upload = False

    print(f"\nFolder : {folder}")
    print(f"PDFs   : {len(pdfs)}")
    if args.pages:
        print(f"Pages  : {args.pages} per document")
    print()

    if args.dry_run:
        from app.services.chunker import chunk_pages
        from app.services.pdf import parse_pdf

        total = 0
        for pdf in pdfs:
            try:
                parsed = parse_pdf(pdf)
                pages = _slice_pages(parsed.pages, args.pages)
                n = len(
                    chunk_pages(
                        pages,
                        settings.chunk_max_tokens,
                        settings.chunk_overlap_tokens,
                    )
                )
                total += n
                print(f"  {pdf.name[:56]:58} {parsed.page_count:>4}p  {n:>4} chunks")
            except PdfParseError as exc:
                print(f"  {pdf.name[:56]:58} UNREADABLE: {exc}")
        print(f"\n  {total} chunks total -> roughly {total} extraction calls\n")
        return 0

    init_db()
    run_started = time.perf_counter()

    with get_conn() as conn:
        print_layer(snapshot(conn), prefix="before   : ")
        print()

        for index, pdf in enumerate(pdfs, start=1):
            before = snapshot(conn)
            data = _read(pdf, args.pages)
            if data is None:
                continue

            print(f"[{index}/{len(pdfs)}] {pdf.name}")
            print(f"          pool before: {before['facts']} existing fact(s) in the layer")

            started = time.perf_counter()
            try:
                result = ingest_pdf(
                    conn,
                    filename=pdf.name,
                    data=data,
                    extract=not args.no_extract,
                )
            except PdfParseError as exc:
                print(f"          FAILED: {exc}\n")
                continue
            elapsed = time.perf_counter() - started

            after = snapshot(conn)
            ex, ln = result.extraction, result.linking

            if result.deduplicated:
                print(f"          already ingested as document {result.document.id}")
            else:
                print(
                    f"          chunks={result.chunk_count}  "
                    f"facts=+{after['facts'] - before['facts']}  "
                    f"embeddings=+{after['embeddings'] - before['embeddings']}  "
                    f"relationships=+{after['relationships'] - before['relationships']}"
                )
                if ex:
                    print(
                        f"          extract : {ex.chunks_processed}/"
                        f"{ex.chunks_processed + ex.chunks_failed} chunks, "
                        f"{ex.facts_inserted} facts, "
                        f"{ex.facts_grounded} grounded"
                        + (", QUOTA EXHAUSTED" if ex.quota_exhausted else "")
                    )
                if ln:
                    print(
                        f"          link    : compared against a pool of "
                        f"{ln.pool_size} existing fact(s); "
                        f"{ln.pairs_evaluated} pair(s) judged, "
                        f"{ln.relationships_created} stored  {ln.by_type or ''}"
                    )
                else:
                    print("          link    : skipped")
                if result.self_check:
                    print(f"          check   : {result.self_check}")

            print(f"          elapsed : {human(elapsed)}")
            print_layer(after, prefix="          layer   : ")
            print()

        print_layer(snapshot(conn), prefix="after    : ")

    print(f"\ntotal wall time: {human(time.perf_counter() - run_started)}\n")
    return 0


def _slice_pages(pages, spec: str | None):
    """Apply a 1-based inclusive 'a-b' page range to parsed pages."""
    if not spec:
        return pages
    first, _, last = spec.partition("-")
    lo = int(first)
    hi = int(last) if last else lo
    return [p for p in pages if lo <= p.page_number <= hi]


def _read(pdf: Path, pages: str | None) -> bytes | None:
    """Return the PDF bytes, cropped to a page range when one is given.

    Cropping produces a genuinely smaller PDF rather than filtering after
    parsing, so the stored document, its page count, and every bbox stay
    consistent with what was actually ingested.
    """
    if not pages:
        return pdf.read_bytes()

    import fitz

    first, _, last = pages.partition("-")
    lo = int(first)
    hi = int(last) if last else lo
    try:
        source = fitz.open(pdf)
    except Exception as exc:
        print(f"          FAILED to open: {exc}")
        return None
    try:
        out = fitz.open()
        out.insert_pdf(source, from_page=lo - 1, to_page=min(hi, source.page_count) - 1)
        # insert_pdf copies pages, not document metadata. Carry it over, or the
        # excerpt loses the real title and falls back to the filename for no
        # reason other than having been cropped.
        if source.metadata:
            out.set_metadata(source.metadata)
        data = out.tobytes()
        out.close()
        return data
    finally:
        source.close()


if __name__ == "__main__":
    raise SystemExit(main())
