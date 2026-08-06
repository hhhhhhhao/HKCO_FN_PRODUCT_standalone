# -*- coding: utf-8 -*-
"""Minimal GPT service wrapper compatible with the reference implementation."""
import time
from typing import Optional

import requests
from loguru import logger

from shared.conf.service_conf import config


def call_gpt_service(
    info_code: str,
    text: str,
    table_name: str,
    field_list: Optional[list],
    system_info: str,
    example_info_list: Optional[list],
    engine: str = "gpt-5.1",
    temperature: str = "0",
    top_p: str = "1",
    max_retries: int = 3,
    timeout: int = 180,
    retry_wait_time: int = 3,
    json_schema: str = "",
    request_id: str = "",
    picture_list: Optional[list] = None,
) -> Optional[dict]:
    """调用参考工程的 GPT 提取服务，失败返回 None。"""
    url = config.gpt_request_url
    if not url:
        logger.warning("gpt_request_url 未配置，跳过 AI 调用")
        return None

    data = {
        "Infocode": info_code,
        "Text": text,
        "TableName": table_name,
        "FieldList": field_list,
        "SystemInfo": system_info,
        "ExampleInfoList": example_info_list,
        "Engine": engine,
        "Temperature": temperature,
        "Top_p": top_p,
        "RequestId": request_id,
        "PictureList": picture_list or [],
    }
    if engine in ("gpt-4o-new", "gpt-5.1") and json_schema:
        data["ResponseFormat"] = json_schema

    headers = {"Content-Type": "application/json"}
    retry_codes = {"28", "31"}
    for retry in range(max_retries):
        try:
            response = requests.post(url, json=data, headers=headers, timeout=timeout)
            payload = response.json()
            if response.status_code == 200 and str(payload.get("Code")) not in retry_codes:
                logger.info("调 gpt 服务成功, info_code=%s, retries=%s", info_code, retry)
                return payload
            code = payload.get("Code", "") if response.status_code == 200 else ""
            logger.error(
                "调 gpt 服务失败, info_code=%s, retries=%s, status_code=%s, code=%s",
                info_code,
                retry,
                response.status_code,
                code,
            )
        except Exception as exc:
            logger.error("调 gpt 服务失败, info_code=%s, retries=%s, ex=%s", info_code, retry, exc)
        time.sleep(retry_wait_time)
    return None
