# -*- coding: utf-8 -*-
"""Stub: Error code enum used by EAPS_HKCO_FN_PRODUCT."""
from enum import Enum


class ErrorCodeType(Enum):
    SUCCESS = 0
    ERROR_CALL_DERIVED_DATA = 5001
    EMPTY_CALL_DERIVED_DATA = 5002
    ERROR_INSERT_PDFJX = 5003
    ERROR_INSERT_NEWSADMIN = 5004
    ERROR_ONLY_PARSING_NO_PUBLISH = 5005
    ERROR_UNKNOWN = 9999
