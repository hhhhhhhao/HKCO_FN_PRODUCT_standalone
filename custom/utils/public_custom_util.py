# -*- coding: utf-8 -*-
"""Stub: Public custom utility functions used by EAPS_HKCO_FN_PRODUCT.

In backtest mode, most SQL/task-center calls are no-ops or return empty data.
call_derived_data_interface is used by get_last_period_data — returns Status=False
to trigger fallback (empty last-period data), which is correct for standalone backtest.
"""


def call_derived_data_interface(call_derived_id, info_code, request_id, data, if_log=True):
    """Stub: always returns failure so last_period_data falls back to empty list."""
    return {"Status": False, "Result": None}


def call_task_center_single_taskid(request_id, info_code, task_info, msg, error_code, if_callback=True, report_path="", page=0):
    pass


def call_task_center_multi_taskid(request_id, info_code, task_info_list, msg, error_code, if_callback=True, report_path="", page=0):
    pass


def delete_sql_ein1(sql):
    """Stub: DELETE queries always succeed with no rows affected."""
    return True, []


def select_sql_ein1(sql):
    """Stub: SELECT queries always return empty result."""
    return True, []


def get_basic_info(info_code):
    return {}


def get_basic_info_by_task(task_info_list):
    return {}


def insert_newsadmin_and_check(info_code, task_id, table_name, derived_data, checkout_sql=""):
    return True, "ok", 0


def insert_pdfjx_and_return_detail(info_code, task_id, table_name, derived_data):
    return True, "ok"
