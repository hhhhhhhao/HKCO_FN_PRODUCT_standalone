# -*- coding: utf-8 -*-
"""Stub: Simple config object matching the interface used by EAPS_HKCO_FN_PRODUCT."""
import os


class _Config:
    profile = "dev"
    log_path = "logs"
    gpt_request_url = os.environ.get(
        "HKCO_GPT_REQUEST_URL",
        "http://172.16.53.187:8082/gpt_extract_entities_auto",
    )


config = _Config()
