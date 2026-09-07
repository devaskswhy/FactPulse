"""Render PDF pages to PNG, and map PDF coordinates onto the result.

The frontend draws a highlight box over a page image. PDF geometry is in
*points* (1/72 inch) and the image is in *pixels*, so something has to convert
between them. Doing it in the browser means shipping the render scale to the
client and trusting every caller to apply it identically; doing it here means
the API hands over coordinates that are already correct for the image it also
serves. This module owns both halves so they cannot drift apart.

Rendered pages are cached on disk under the document's hash. Re-rendering a
page of a 100-page report on every request would be slow and pointless -- the
source PDF is immutable and content-addressed, so the image is too.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import fitz

from app.core.config import settings

logger = logging.getLogger(__name__)


class PageRenderError(RuntimeError):
    """The page could not be rendered."""


@dataclass(frozen=True)
class RenderedPage:
    path: Path
    page_number: int  # 1-based
    width: int        # pixels
    height: int       # pixels
    scale: float      # pixels per PDF point


def _cache_path(sha256: str, page_number: int, scale: float) -> Path:
    # Scale is part of the name: changing PAGE_RENDER_SCALE must not serve a
    # stale image whose pixels no longer match the coordinates we compute.
    tag = f"{scale:g}".replace(".", "_")
    return settings.page_cache_path / sha256 / f"p{page_number}@{tag}.png"


def render_page(
    pdf_path: Path | str,
    page_number: int,
    sha256: str,
    scale: float | None = None,
) -> RenderedPage:
    """Render one 1-based page to PNG, caching the result.

    `scale` is pixels per PDF point: 2.0 renders a 612x792 page at 1224x1584,
    which is legible on a normal display without being enormous.
    """
    scale = scale or settings.page_render_scale
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise PageRenderError(f"PDF not found: {pdf_path}")

    cached = _cache_path(sha256, page_number, scale)
    try:
        document = fitz.open(pdf_path)
    except Exception as exc:
        raise PageRenderError(f"could not open PDF: {exc}") from exc

    try:
        if not 1 <= page_number <= document.page_count:
            raise PageRenderError(
                f"page {page_number} out of range (document has "
                f"{document.page_count} pages)"
            )
        page = document.load_page(page_number - 1)

        if cached.exists():
            # Trust the cached pixels, but take the dimensions from the page
            # geometry so callers always get numbers consistent with the boxes
            # scale_bbox() produces.
            rect = page.rect
            return RenderedPage(
                path=cached,
                page_number=page_number,
                width=int(round(rect.width * scale)),
                height=int(round(rect.height * scale)),
                scale=scale,
            )

        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        cached.parent.mkdir(parents=True, exist_ok=True)
        pixmap.save(cached)
        return RenderedPage(
            path=cached,
            page_number=page_number,
            width=pixmap.width,
            height=pixmap.height,
            scale=scale,
        )
    finally:
        document.close()


def page_dimensions(
    pdf_path: Path | str, page_number: int, scale: float | None = None
) -> tuple[int, int, float]:
    """(width_px, height_px, scale) for a page, without rendering it."""
    scale = scale or settings.page_render_scale
    try:
        document = fitz.open(Path(pdf_path))
    except Exception as exc:
        raise PageRenderError(f"could not open PDF: {exc}") from exc
    try:
        if not 1 <= page_number <= document.page_count:
            raise PageRenderError(f"page {page_number} out of range")
        rect = document.load_page(page_number - 1).rect
        return (
            int(round(rect.width * scale)),
            int(round(rect.height * scale)),
            scale,
        )
    finally:
        document.close()


def scale_bbox(
    bbox: tuple[float, float, float, float], scale: float | None = None
) -> tuple[float, float, float, float]:
    """Convert a PDF-point box to pixel coordinates in the rendered image.

    PyMuPDF's page space and its pixmap share an origin at the top-left with y
    increasing downwards, so this is a pure multiply -- no axis flip. (PDF's own
    native coordinate system is bottom-left origin, but `page.rect` and
    `search_for` already report in the top-left space, which is what is stored
    in facts.bbox_*.)
    """
    scale = scale or settings.page_render_scale
    x0, y0, x1, y1 = bbox
    return (
        round(x0 * scale, 2),
        round(y0 * scale, 2),
        round(x1 * scale, 2),
        round(y1 * scale, 2),
    )
