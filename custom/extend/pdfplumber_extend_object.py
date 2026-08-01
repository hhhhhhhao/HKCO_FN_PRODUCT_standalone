# -*- coding: utf-8 -*-
"""Stub: pdfplumber wrapper compatible with the original ExtendPlumber API."""


class ExtendPlumber:
    """Drop-in replacement for the original ExtendPlumber pdfplumber wrapper."""

    _pdf = None

    @classmethod
    def open(cls, pdf_path):
        import pdfplumber
        inst = cls()
        inst._pdf = pdfplumber.open(pdf_path)
        return inst

    @property
    def pages(self):
        return self._pdf.pages if self._pdf else []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self._pdf:
            self._pdf.close()
