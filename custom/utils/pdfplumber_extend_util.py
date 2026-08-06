"""PDF table edge helpers used by HKCO_FN_PRODUCT_selector.

This is a standalone subset of the reference implementation in
E:\\emdp_auto\\src\\custom\\utils\\pdfplumber_extend_util.py. It keeps the
surface used by the local selector without pulling in the full dependency
tree of the original service.
"""
from __future__ import annotations

from custom.extend.pdfplumber_extend_object import ExtendPlumberPage


def _is_white(r, g, b):
    return (r * 255 * 0.299 + g * 255 * 0.587 + b * 255 * 0.114) > 186


def _color_channel(value, index):
    if isinstance(value, (list, tuple)):
        if len(value) == 1:
            return value[0]
        return value[index]
    if isinstance(value, (int, float)):
        return value
    return None


def clear_rect_edges(current_page, remove_small_rect_optimized=True):
    """Drop white/filled rectangles that do not form table edges."""

    def check_rect_is_line(rect, skip_edge_rect_check=False):
        try:
            if rect["width"] <= 3 and rect["height"] <= 3:
                if remove_small_rect_optimized:
                    if rect["width"] < 1.0 or rect["height"] < 1.0:
                        return True
                    return False
                return False
            if not skip_edge_rect_check:
                page_width = current_page.width
                page_height = current_page.height
                if (
                    rect["x0"] <= 0.1
                    or rect["y0"] <= 0.1
                    or rect["x1"] >= page_width - 0.1
                    or rect["y1"] >= page_height - 0.1
                ):
                    return False
            if rect["fill"]:
                if rect["width"] > 3 and rect["height"] > 3:
                    return False
                color = rect.get("non_stroking_color")
                r = _color_channel(color, 0)
                g = _color_channel(color, 1)
                b = _color_channel(color, 2)
                if r is None or g is None or b is None:
                    return True
                return not _is_white(r, g, b)
            if rect.get("stroke") and rect.get("stroking_color") is not None:
                color = rect.get("stroking_color")
                r = _color_channel(color, 0)
                g = _color_channel(color, 1)
                b = _color_channel(color, 2)
                if r is None or g is None or b is None:
                    return True
                return not _is_white(r, g, b)
            return False
        except Exception:
            return False

    from pdfplumber.page import CroppedPage

    new_rects = []
    for rect in list(current_page.rects or []):
        if check_rect_is_line(
            rect,
            skip_edge_rect_check=isinstance(current_page, CroppedPage),
        ):
            new_rects.append(rect)
    current_page.rects.clear()
    current_page.rects.extend(new_rects)


def generate_extend_plumber_page(current_page, *args, **kwargs):
    """Wrap a pdfplumber page with the extended table API surface."""
    return ExtendPlumberPage(current_page)
