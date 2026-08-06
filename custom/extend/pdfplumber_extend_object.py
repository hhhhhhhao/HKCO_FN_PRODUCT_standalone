# -*- coding: utf-8 -*-
"""Standalone pdfplumber context-manager adapter."""


class ExtendPlumber:
    """Expose the context-manager API used by the extraction entrypoint."""

    _pdf = None
    _add_edge_line_flag = False
    _add_image_line_flag = False

    @classmethod
    def open(cls, pdf_path):
        import pdfplumber
        inst = cls()
        inst._pdf = pdfplumber.open(pdf_path)
        return inst

    @property
    def pages(self):
        return self._pdf.pages if self._pdf else []

    def set_add_edge_line_flag(self, value):
        self._add_edge_line_flag = bool(value)

    def set_add_image_line_flag(self, value):
        self._add_image_line_flag = bool(value)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._pdf:
            self._pdf.close()


class ExtendPlumberPage:
    """Lightweight wrapper matching the reference ExtendPlumberPage surface."""

    def __init__(self, page, *args, **kwargs):
        self.page = page

    def find_tables(self, *args, **kwargs):
        return self.page.find_tables(*args, **kwargs)

    def extract_tables(self, *args, **kwargs):
        return self.page.extract_tables(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.page, name)
