from custom.service.HKCO_FN_PRODUCT_evidence import table_refs_from_sources
from custom.service.HKCO_FN_PRODUCT_pipeline import build_default_pipeline
from custom.service.EAPS_HKCO_FN_PRODUCT_get_res import get_res
from custom.service.HKCO_FN_PRODUCT_document import get_all_source_tables


def run(source):
    return build_default_pipeline().run(table_refs_from_sources([source]))


def test_document_scan_uses_semantic_heading_not_nearest_measurement_banner():
    lines = [
        {"page_number": 3, "text": "收入分拆資料"},
        {"page_number": 3, "text": "截至二零二五年十二月三十一日止年度"},
        {"page_number": 3, "text": "人民幣千元"},
        {"page_number": 3, "text": "table", "is_table": True,
         "table": [["", "2025年"], ["產品甲", "40"], ["產品乙", "60"]]},
    ]
    assert get_all_source_tables(lines)[0]["title"] == "收入分拆資料"


def test_row_period_has_one_class_and_one_extractor():
    output, decision = run({
        "page_number": 3,
        "title": "按產品劃分的收入",
        "target_table": [
            ["產品", "2025年千元", "2024年千元"],
            ["軟件產品", "120", "100"],
            ["服務產品", "30", "20"],
            ["合計", "150", "120"],
        ],
    })
    assert [item.table_type for item in decision.classifications] == ["row_period"]
    assert decision.selected.classification.table_type == "row_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("軟件產品", 120.0), ("服務產品", 30.0), ("合计", 150.0),
    ]


def test_row_metric_period_reads_only_revenue_column():
    output, decision = run({
        "page_number": 5,
        "title": "產品收入和成本分析",
        "target_table": [
            ["", "2025年", "2025年", "2024年", "2024年"],
            ["產品", "收入千元", "成本千元", "收入千元", "成本千元"],
            ["甲產品", "40", "25", "30", "20"],
            ["乙產品", "60", "35", "50", "30"],
            ["合計", "100", "60", "80", "50"],
        ],
    })
    assert decision.classifications[0].table_type == "row_metric_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("甲產品", 40.0), ("乙產品", 60.0), ("合计", 100.0),
    ]


def test_in_table_revenue_breakdown_heading_is_current_revenue_evidence():
    output, decision = run({
        "page_number": 10,
        "title": "以下討論及分析應與財務資料一併閱讀。",
        "target_table": [
            ["按商品劃分之收入截至十二月三十一日止年度", "二零二五年百萬美元", "二零二四年百萬美元", "變動%"],
            ["銅", "40", "30", "33%"], ["鋅", "60", "50", "20%"],
            ["總計", "100", "80", "25%"],
        ],
    })
    assert decision.classifications[0].supported
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("銅", 40.0), ("鋅", 60.0), ("合计", 100.0),
    ]


def test_amount_volume_price_columns_are_measurements_not_column_identities():
    output, decision = run({
        "page_number": 8,
        "title": "收益",
        "target_table": [
            ["", "截至二零二四年十二月三十一日止年度", "截至二零二四年十二月三十一日止年度", "截至二零二四年十二月三十一日止年度",
             "截至二零二三年十二月三十一日止年度", "截至二零二三年十二月三十一日止年度", "截至二零二三年十二月三十一日止年度"],
            ["百萬美元", "千噸", "平均售價(美元/噸)", "百萬美元", "千噸", "平均售價(美元/噸)"],
            ["銷售原鋁", "70", "7", "10", "60", "6", "10"],
            ["銷售氧化鋁", "30", "3", "10", "20", "2", "10"],
            ["總收益", "100", "", "", "80", "", ""],
        ],
    })
    assert decision.classifications[0].table_type == "row_measurement_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2024-12-31"] == [
        ("銷售原鋁", 70.0), ("銷售氧化鋁", 30.0), ("合计", 100.0),
    ]
    assert {row["unit"] for row in output} == {"004"}


def test_revenue_type_is_product_section_in_multi_axis_breakdown():
    output, decision = run({
        "page_number": 10,
        "title": "收益分拆資料",
        "target_table": [
            ["收益類型", "2025年千元"],
            ["銷售貨物", "70"], ["服務收入", "30"], ["總計", "100"],
            ["地區市場", "2025年千元"],
            ["中國", "60"], ["海外", "40"], ["總計", "100"],
        ],
    })
    assert decision.classifications[0].table_type == "multi_section_row"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("銷售貨物", 70.0), ("服務收入", 30.0), ("合计", 100.0),
    ]


def test_arithmetic_hierarchy_uses_prior_identity_to_choose_leaf_frontier():
    source = {
        "page_number": 12,
        "title": "收入分析",
        "target_table": [
            ["", "2025年百萬元"],
            ["客户合約收入", "100"],
            ["銷售產品", "80"], ["產品甲", "50"], ["產品乙", "30"],
            ["提供服務", "20"], ["服務甲", "12"], ["服務乙", "8"],
            ["按客户類型劃分的收入總額", "100"],
            ["第三方", "70"], ["關聯方", "30"],
        ],
    }
    output, _ = build_default_pipeline().run(
        table_refs_from_sources([source]),
        context={"prior_product_names": ["產品甲", "產品乙", "服務甲", "服務乙"]},
    )
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("產品甲", 50.0), ("產品乙", 30.0),
        ("服務甲", 12.0), ("服務乙", 8.0), ("合计", 100.0),
    ]


def test_arithmetic_hierarchy_keeps_matched_parents_and_new_peer_parent():
    source = {
        "page_number": 1,
        "title": "收入摘要",
        "target_table": [
            ["", "2025年千元"], ["收入", "120"],
            ["平台業務", "90"], ["軟件", "50"], ["服務", "40"],
            ["融資業務", "20"], ["租賃", "12"], ["其他融資", "8"],
            ["新增業務", "10"], ["毛利", "40"],
        ],
    }
    output, _ = build_default_pipeline().run(
        table_refs_from_sources([source]),
        context={"prior_product_names": ["平台業務", "融資業務"]},
    )
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("平台業務", 90.0), ("融資業務", 20.0),
        ("新增業務", 10.0), ("合计", 120.0),
    ]


def test_prior_row_identity_disambiguates_product_rows_from_business_columns():
    source = {
        "page_number": 53,
        "title": "可呈報分部資料",
        "target_table": [
            ["", "截至二零二五年十二月三十一日止年度", "截至二零二五年十二月三十一日止年度", "截至二零二五年十二月三十一日止年度"],
            ["百萬美元", "礦山甲", "礦山乙", "本集團"],
            ["銅", "30", "10", "40"], ["鋅", "20", "40", "60"],
            ["來自客户合約的收入", "50", "50", "100"],
        ],
    }
    output, decision = build_default_pipeline().run(
        table_refs_from_sources([source]),
        context={"prior_product_names": ["銅", "鋅"]},
    )
    assert decision.selected.classification.table_type == "row_identity_total_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("銅", 40.0), ("鋅", 60.0), ("合计", 100.0),
    ]


def test_segment_matrix_period_reads_revenue_and_explicit_profit_rows():
    output, decision = run({
        "page_number": 8,
        "title": "按業務分部劃分的收入及業績",
        "target_table": [
            ["截至2025年12月31日止年度", "", "", ""],
            ["", "航運業務", "碼頭業務", "總計"],
            ["營業額", "100", "50", "150"],
            ["銷售成本", "(80)", "(42)", "(122)"],
            ["毛利", "20", "8", "28"],
        ],
    })
    assert decision.classifications[0].table_type == "segment_matrix_period"
    assert [(row["product_name"], row["mbrevenue"], row["mbcost"], row["gross_profit"]) for row in output] == [
        ("航運業務", 100.0, 80.0, 20.0), ("碼頭業務", 50.0, 42.0, 8.0), ("合计", 150.0, 122.0, 28.0),
    ]


def test_segment_matrix_preserves_explicit_null_elimination_identity():
    source = {
        "page_number": 8,
        "title": "截至2025年12月31日止年度的業務分部收入",
        "target_table": [
            ["", "業務甲千元", "業務乙千元", "分部間抵銷千元", "合計千元"],
            ["來自外部客户的收入", "40", "60", "-", "100"],
        ],
    }
    output, _ = build_default_pipeline().run(
        table_refs_from_sources([source]),
        context={"prior_product_names": ["業務甲", "業務乙", "分部間抵銷"]},
    )
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("業務甲", 40.0), ("業務乙", 60.0), ("分部間抵銷", None), ("合计", 100.0),
    ]


def test_multi_section_projects_product_section_and_excludes_geography():
    output, decision = run({
        "page_number": 10,
        "title": "收入分拆",
        "target_table": [
            ["按產品類型劃分", "2025年"], ["產品A", "10"], ["產品B", "20"], ["合計", "30"],
            ["按地區劃分", "2025年"], ["中國", "18"], ["海外", "12"], ["合計", "30"],
        ],
    })
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("產品A", 10.0), ("產品B", 20.0), ("合计", 30.0),
    ]
    assert decision.classifications[0].table_type == "multi_section_row"
    assert decision.classifications[0].supported is True


def test_customer_location_revenue_is_a_geographic_axis_not_product_identity():
    output, decision = run({
        "page_number": 10,
        "title": "按客户位置劃分的收益金額",
        "target_table": [
            ["", "2025年千元"], ["中國", "70"], ["海外", "30"], ["合計", "100"],
        ],
    })
    assert output == []
    assert decision.classifications[0].semantic_axis == "geography"


def test_anchored_group_revenue_analysis_title_resolves_unknown_axis():
    output, decision = run({
        "page_number": 9,
        "title": "本集團收入之分析如下:",
        "target_table": [
            ["", "二零二五年千港元", "二零二四年千港元"],
            ["來自客户合約之收入", "", ""],
            ["樓宇建築合約工程", "70", "50"],
            ["修建工程之合約工程", "30", "20"],
            ["總計", "100", "70"],
        ],
    })
    assert decision.classifications[0].semantic_axis == "product_service"
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("樓宇建築合約工程", 70.0), ("修建工程之合約工程", 30.0), ("合计", 100.0),
    ]


def test_operating_segment_statement_uses_external_customer_revenue():
    output, decision = run({
        "page_number": 7,
        "title": "營運分部",
        "target_table": [
            ["截至2025年12月31日止年度", "", "", "", "", ""],
            ["", "截至二零二五年十二月三十一日止年度", "", "", "", ""],
            ["集裝箱航運業務人民幣千元", "碼頭業務人民幣千元", "公司及其他業務人民幣千元", "分部間抵銷人民幣千元", "總計人民幣千元"],
            ["利潤表", "", "", "", "", ""],
            ["總收入", "110", "60", "-", "(20)", "150"],
            ["-分部間收入", "10", "10", "-", "(20)", "-"],
            ["-收入(來自外部客户)", "100", "50", "-", "-", "150"],
            ["分部營運利潤", "20", "8", "1", "(1)", "28"],
        ],
    })
    assert decision.classifications[0].table_type == "segment_matrix_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("集裝箱航運業務", 100.0), ("碼頭業務", 50.0), ("合计", 150.0),
    ]


def test_segment_units_do_not_replace_identity_header_and_segment_total_is_total():
    output, decision = run({
        "page_number": 12,
        "title": "按經營分部劃分的收入及業績",
        "target_table": [
            ["", "加盟", "貿易", "其他", "分部合計"],
            ["人民幣千元", "人民幣千元", "人民幣千元", "人民幣千元"],
            ["截至二零二五年十二月三十一日止年度", "", "", "", ""],
            ["合同收入", "80", "30", "10", "120"],
            ["減:分部間收入", "-", "(5)", "-", "(5)"],
            ["來自外部客户收入", "80", "25", "10", "115"],
        ],
    })
    assert decision.selected.classification.table_type == "segment_matrix_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("加盟", 80.0), ("貿易", 25.0), ("其他", 10.0), ("合计", 115.0),
    ]


def test_row_detail_stops_when_disclosure_switches_to_recognition_time():
    output, _ = run({
        "page_number": 10,
        "title": "來自客户合約收益之細分",
        "target_table": [
            ["", "二零二五年千美元", "二零二四年千美元"],
            ["產品甲", "30", "20"],
            ["產品乙", "70", "50"],
            ["", "100", "70"],
            ["收入確認時間", "", ""],
            ["於某一時間點", "100", "70"],
        ],
    })
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("產品甲", 30.0), ("產品乙", 70.0), ("合计", 100.0),
    ]


def test_amountless_parent_names_bullet_leaves_in_hierarchy():
    output, _ = run({
        "page_number": 8,
        "title": "收益之分析",
        "target_table": [
            ["", "2025年千港元", "2024年千港元"],
            ["服務費及佣金", "", ""],
            ["-資產管理", "30", "20"],
            ["-企業融資", "20", "10"],
            ["包銷收入", "", ""],
            ["-企業融資", "10", "5"],
            ["投資收入", "40", "35"],
            ["", "100", "70"],
        ],
    })
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("服務費及佣金-資產管理", 30.0),
        ("服務費及佣金-企業融資", 20.0),
        ("包銷收入-企業融資", 10.0),
        ("投資收入", 40.0),
        ("合计", 100.0),
    ]


def test_finer_product_detail_outranks_coarser_business_matrix():
    sources = [
        {
            "page_number": 7,
            "title": "按產品劃分的收入",
            "target_table": [
                ["", "2025年千元"],
                ["產品甲", "20"], ["產品乙", "30"], ["產品丙", "50"], ["合計", "100"],
            ],
        },
        {
            "page_number": 9,
            "title": "可呈報經營分部資料",
            "target_table": [
                ["截至2025年12月31日止年度", "", ""],
                ["", "製造業務", "服務業務", "總計"],
                ["來自外部客户的收入", "60", "40", "100"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(table_refs_from_sources(sources))
    assert decision.selected.classification.table_type == "row_period"
    assert [row["product_name"] for row in output] == ["產品甲", "產品乙", "產品丙", "合计"]


def test_prior_identities_softly_choose_disclosure_axis_without_closing_current_set():
    sources = [
        {
            "page_number": 7,
            "title": "收入明細",
            "target_table": [
                ["", "2025年千元"],
                ["售票收入", "20"], ["餐飲收入", "30"], ["廣告收入", "50"], ["合計", "100"],
            ],
        },
        {
            "page_number": 9,
            "title": "經營分部收入",
            "target_table": [
                ["截至2025年12月31日止年度", "", ""],
                ["", "影院投資及管理業務", "新媒體業務", "總計"],
                ["來自外部客户的收入", "60", "40", "100"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={"prior_product_names": ["影院投資及管理業務", "新媒體業務"]},
    )
    assert decision.selected.classification.table_type == "segment_matrix_period"
    assert [row["product_name"] for row in output] == ["影院投資及管理業務", "新媒體業務", "合计"]


def test_more_prior_identity_hits_select_segment_over_product_named_table():
    sources = [
        {
            "page_number": 7,
            "title": "按產品劃分的收入",
            "target_table": [["", "2025年千元"], ["舊產品甲", "100"], ["合計", "100"]],
        },
        {
            "page_number": 9,
            "title": "經營分部收入",
            "target_table": [
                ["截至2025年12月31日止年度", "", "", ""],
                ["", "舊產品甲", "舊產品乙", "新增分部", "總計"],
                ["來自外部客户的收入", "40", "30", "30", "100"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={"prior_product_names": ["舊產品甲", "舊產品乙"]},
    )
    assert decision.selected.classification.evidence.table.table_id == "p9:1"
    assert [row["product_name"] for row in output] == [
        "舊產品甲", "舊產品乙", "新增分部", "合计",
    ]


def test_identity_rich_asset_table_cannot_beat_actual_revenue_candidate():
    sources = [
        {
            "page_number": 10,
            "title": "分部資產",
            "target_table": [
                ["", "2025年千元"], ["業務甲", "40"], ["業務乙", "60"], ["合計", "100"],
            ],
        },
        {
            "page_number": 12,
            "title": "分部收入",
            "target_table": [
                ["", "2025年千元"], ["業務甲", "100"], ["新增業務", "50"], ["合計", "150"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={"prior_product_names": ["業務甲", "業務乙"]},
    )
    assert decision.selected.classification.evidence.table.table_id == "p12:1"
    assert [row["product_name"] for row in output] == ["業務甲", "新增業務", "合计"]


def test_prior_identified_row_matrix_uses_only_explicit_total_column():
    source = {
        "page_number": 15,
        "title": "商品及服務類型",
        "target_table": [
            ["", "截至二零二五年十二月三十一日止年度", "截至二零二五年十二月三十一日止年度", "截至二零二五年十二月三十一日止年度", "截至二零二五年十二月三十一日止年度"],
            ["遊戲業務人民幣百萬元", "教育業務人民幣百萬元", "物業業務人民幣百萬元", "總額人民幣百萬元"],
            ["遊戲卡收益", "20", "-", "-", "20"],
            ["教育服務", "-", "30", "-", "30"],
            ["物業銷售", "-", "-", "50", "50"],
            ["", "20", "30", "50", "100"],
        ],
    }
    output, decision = build_default_pipeline().run(
        table_refs_from_sources([source]),
        context={"prior_product_names": ["遊戲卡收益", "教育服務", "物業銷售"]},
    )
    assert decision.selected.classification.table_type == "row_identity_total_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("遊戲卡收益", 20.0), ("教育服務", 30.0), ("物業銷售", 50.0), ("合计", 100.0),
    ]


def test_plan_materializes_primary_only_and_metric_table_never_competes_for_revenue():
    sources = [
        {
            "page_number": 8,
            "title": "按產品劃分的收入",
            "target_table": [
                ["", "2025年千元"], ["產品甲", "40"], ["產品乙", "60"], ["合計", "100"],
            ],
        },
        {
            "page_number": 12,
            "title": "按產品劃分的銷售成本",
            "target_table": [
                ["", "2025年千元"], ["產品甲", "25"], ["產品乙", "35"], ["合計", "60"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(table_refs_from_sources(sources))
    assert decision.debug["materialized_tables"] == ["p8:0"]
    assert "p12:1" not in decision.debug["planned_tables"]
    assert [(row["product_name"], row["mbcost"]) for row in output] == [
        ("產品甲", 25.0), ("產品乙", 35.0), ("合计", 60.0),
    ]
    assert all(row["gross_profit"] == "" for row in output)


def test_explicit_compatible_supplement_joins_primary_revenue_plan_by_current_totals():
    sources = [
        {
            "page_number": 8,
            "title": "經營分部收入",
            "target_table": [
                ["截至2025年12月31日止年度", "", ""],
                ["千元", "業務甲", "業務乙", "總計"],
                ["來自外部客户的收入", "40", "60", "100"],
            ],
        },
        {
            "page_number": 10,
            "title": "按分部劃分的補充收入資料",
            "target_table": [
                ["", "2025年千元"], ["子項甲", "30"], ["子項乙", "70"], ["總收入", "100"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={"prior_product_names": ["業務甲", "業務乙"]},
    )
    assert decision.debug["materialized_tables"] == ["p8:0"]
    assert decision.debug["supplemental_tables"] == ["p10:1"]
    assert [row["product_name"] for row in output] == [
        "業務甲", "業務乙", "合计", "子項甲", "子項乙",
    ]


def test_incompatible_supplement_does_not_join_primary_revenue_plan():
    sources = [
        {
            "page_number": 8,
            "title": "經營分部收入",
            "target_table": [
                ["截至2025年12月31日止年度", "", ""],
                ["", "業務甲", "業務乙", "總計"],
                ["來自外部客户的收入", "40", "60", "100"],
            ],
        },
        {
            "page_number": 10,
            "title": "按分部劃分的補充收入資料",
            "target_table": [
                ["", "2025年千元"], ["其他甲", "30"], ["其他乙", "50"], ["總收入", "80"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={"prior_product_names": ["業務甲", "業務乙"]},
    )
    assert decision.debug["supplemental_tables"] == []
    assert [row["product_name"] for row in output] == ["業務甲", "業務乙", "合计"]


def test_explicit_continuation_tables_form_one_primary_plan_before_closure():
    sources = [
        {
            "page_number": 8,
            "title": "按產品劃分的收入",
            "target_table": [
                ["", "2025年千元"], ["產品甲", "20"], ["產品乙", "30"],
            ],
        },
        {
            "page_number": 9,
            "title": "按產品劃分的收入(續)",
            "target_table": [
                ["", "2025年千元"], ["產品丙", "50"], ["合計", "100"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(table_refs_from_sources(sources))
    assert decision.debug["materialized_tables"] == ["p8:0", "p9:1"]
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("產品甲", 20.0), ("產品乙", 30.0), ("產品丙", 50.0), ("合计", 100.0),
    ]


def test_equal_total_does_not_merge_a_different_semantic_axis():
    sources = [
        {
            "page_number": 8,
            "title": "按產品劃分的收入",
            "target_table": [
                ["", "2025年千元"], ["產品甲", "40"], ["產品乙", "60"], ["合計", "100"],
            ],
        },
        {
            "page_number": 10,
            "title": "按地區劃分的補充收入",
            "target_table": [
                ["", "2025年千元"], ["中國", "30"], ["海外", "70"], ["合計", "100"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(table_refs_from_sources(sources))
    assert decision.debug["supplemental_tables"] == []
    assert [row["product_name"] for row in output] == ["產品甲", "產品乙", "合计"]


def test_explicit_cost_table_is_converted_to_primary_revenue_unit():
    sources = [
        {
            "page_number": 8,
            "title": "按產品劃分的收入",
            "target_table": [
                ["", "2025年千元"], ["產品甲", "4000"], ["產品乙", "6000"], ["合計", "10000"],
            ],
        },
        {
            "page_number": 12,
            "title": "按產品劃分的銷售成本",
            "target_table": [
                ["", "2025年百萬元"], ["產品甲", "2.5"], ["產品乙", "3.5"], ["合計", "6"],
            ],
        },
    ]
    output, _ = build_default_pipeline().run(table_refs_from_sources(sources))
    assert [(row["product_name"], row["mbcost"]) for row in output] == [
        ("產品甲", 2500.0), ("產品乙", 3500.0), ("合计", 6000.0),
    ]


def test_explicit_metric_with_different_currency_is_not_merged():
    sources = [
        {
            "page_number": 8,
            "title": "按產品劃分的收入",
            "target_table": [
                ["", "2025年人民幣千元"], ["產品甲", "40"], ["產品乙", "60"], ["合計", "100"],
            ],
        },
        {
            "page_number": 12,
            "title": "按產品劃分的銷售成本",
            "target_table": [
                ["", "2025年美元千元"], ["產品甲", "25"], ["產品乙", "35"], ["合計", "60"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(table_refs_from_sources(sources))
    assert all(row["mbcost"] == "" for row in output)
    assert {item["reason"] for item in decision.debug["metric_merge_rejections"]} == {
        "currency_mismatch"
    }


def test_prior_amounts_never_change_plan_or_extracted_values():
    sources = [{
        "page_number": 8,
        "title": "按產品劃分的收入",
        "target_table": [
            ["", "2025年人民幣千元"], ["新產品甲", "40"], ["產品乙", "60"], ["合計", "100"],
        ],
    }]
    base_prior = [
        {"PRODUCTNAME": "產品甲", "MBREVENUE": "1", "CURRENCY": "人民币", "UNIT": "002"},
        {"PRODUCTNAME": "產品乙", "MBREVENUE": "2", "CURRENCY": "人民币", "UNIT": "002"},
    ]
    changed_prior = [dict(item, MBREVENUE=str(index * 999999))
                     for index, item in enumerate(base_prior, start=1)]
    first = get_res(None, "TEST", [], last_period_data=base_prior, source_tables=sources)
    second = get_res(None, "TEST", [], last_period_data=changed_prior, source_tables=sources)
    assert first["target_res"] == second["target_res"]
    assert [item["product_name"] for item in first["target_res"]] == [
        "新產品甲", "產品乙", "合计"
    ]


def test_row_revenue_accepts_disclosed_hegong_total():
    output, _ = run({
        "page_number": 4,
        "title": "營業額按產品劃分",
        "target_table": [
            ["", "2025年千元"], ["產品甲", "40"], ["產品乙", "60"], ["合共", "100"],
        ],
    })
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("產品甲", 40.0), ("產品乙", 60.0), ("合计", 100.0),
    ]


def test_single_disclosed_product_closes_without_a_printed_total_row():
    output, _ = run({
        "page_number": 4,
        "title": "客户合約收益明細",
        "target_table": [
            ["", "2025年千元", "2024年千元"],
            ["建築工程", "40", "30"],
            ["收益確認時間", "", ""],
            ["隨時間確認", "40", "30"],
        ],
    })
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("建築工程", 40.0), ("合计", 40.0),
    ]


def test_broad_multi_axis_heading_uses_body_identity_dimension():
    sources = [
        {
            "page_number": 17,
            "title": "按類型、銷售渠道及地區市場劃分的收益明細",
            "target_table": [
                ["", "2025年千元"], ["棋盤遊戲", "70"],
                ["模型戰棋遊戲", "20"], ["其他產品", "10"], ["總計", "100"],
            ],
        },
        {
            "page_number": 18,
            "title": "按銷售渠道劃分",
            "target_table": [
                ["", "2025年千元"], ["眾籌平台", "60"],
                ["網店及遊戲展", "10"], ["批發", "30"], ["總計", "100"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(table_refs_from_sources(sources))
    assert decision.classifications[0].semantic_axis == "product_service"
    assert decision.classifications[1].semantic_axis == "sales_channel"
    assert [row["product_name"] for row in output] == [
        "棋盤遊戲", "模型戰棋遊戲", "其他產品", "合计",
    ]


def test_transition_method_revenue_analysis_is_not_a_product_table():
    output, decision = run({
        "page_number": 162,
        "title": "按不同過渡方法確認的保險服務收入分析",
        "target_table": [
            ["", "2025年"], ["修正追溯調整法的合同", "10"],
            ["公允價值法的合同", "20"], ["其他保險合同", "70"], ["合計", "100"],
        ],
    })
    assert output == []
    assert decision.classifications[0].semantic_axis == "measurement_method"


def test_other_income_continuation_cannot_be_primary_revenue():
    output, decision = run({
        "page_number": 14,
        "title": "收入、其他收入及收益淨額（續）",
        "target_table": [
            ["", "2025年千元"], ["其他收入", ""],
            ["銀行利息收入", "10"], ["租金收入", "20"], ["其他收入總值", "30"],
        ],
    })
    assert output == []
    assert "continuation_contains_only_other_income" in decision.classifications[0].reasons


def test_same_axis_finer_detail_replaces_coarse_revenue_rows():
    sources = [
        {
            "page_number": 27,
            "title": "按產品類別劃分的收入",
            "target_table": [
                ["", "2025年千元"], ["處理服務", "40"],
                ["能源銷售", "60"], ["總計", "100"],
            ],
        },
        {
            "page_number": 28,
            "title": "貨品或服務類型補充收入",
            "target_table": [
                ["", "2025年千元"], ["生活垃圾處理", "25"],
                ["危險廢物處理", "15"], ["電力銷售", "50"],
                ["蒸氣銷售", "10"], ["總計", "100"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(table_refs_from_sources(sources))
    assert decision.debug["supplemental_tables"] == ["p28:1"]
    assert [row["product_name"] for row in output] == [
        "生活垃圾處理", "危險廢物處理", "電力銷售", "蒸氣銷售", "合计",
    ]


def test_metric_ledger_is_deferred_instead_of_treating_every_metric_as_revenue():
    output, decision = run({
        "page_number": 7,
        "title": "分類收益及業績分析",
        "target_table": [
            ["", "2025年千元"], ["工程服務分類收益", "100"],
            ["工程服務分類業績", "20"], ["利息收入", "2"],
            ["企業開支", "-5"], ["除稅前溢利", "17"],
        ],
    })
    assert output == []
    assert "metric_ledger_requires_identity_materializer" in decision.classifications[0].reasons


def test_merged_metric_headers_map_each_period_to_its_metric_group():
    output, decision = run({
        "page_number": 9,
        "title": "分部收入及業績",
        "target_table": [
            ["", "分部收入", "分部業績"],
            ["", "2025年千元", "2024年千元", "2025年千元", "2024年千元"],
            ["健康業務", "40", "30", "8", "6"],
            ["數據業務", "60", "50", "12", "10"],
            ["總計", "100", "80", "20", "16"],
        ],
    })
    assert decision.selected.classification.table_type == "row_metric_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("健康業務", 40.0), ("數據業務", 60.0), ("合计", 100.0),
    ]


def test_amount_and_percentage_columns_are_a_row_period_table():
    output, decision = run({
        "page_number": 17,
        "title": "按產品類型劃分的收益明細",
        "target_table": [
            ["", "2025年", "2025年", "2024年", "2024年"],
            ["", "千元", "%", "千元", "%"],
            ["產品甲", "40", "40", "30", "37.5"],
            ["產品乙", "60", "60", "50", "62.5"],
            ["總計", "100", "100", "80", "100"],
        ],
    })
    assert decision.selected.classification.table_type == "row_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("產品甲", 40.0), ("產品乙", 60.0), ("合计", 100.0),
    ]


def test_prior_identity_hits_cannot_turn_employee_count_into_revenue():
    source = {
        "page_number": 106,
        "title": "按職能劃分的全職僱員人數",
        "target_table": [
            ["", "僱員人數", "佔總數百分比"],
            ["公司銀行業務", "40", "40"], ["零售銀行業務", "60", "60"],
            ["合計", "100", "100"],
        ],
    }
    output, decision = build_default_pipeline().run(
        table_refs_from_sources([source]),
        context={"prior_product_names": ["公司銀行業務", "零售銀行業務"]},
    )
    assert output == []
    assert "explicit_non_revenue_measure" in decision.classifications[0].reasons


def test_prior_identity_hits_cannot_turn_segment_assets_into_revenue():
    source = {
        "page_number": 147,
        "title": "報告分部的收入、利潤、資產及負債的信息（續）",
        "target_table": [
            ["", "2025年", "2024年"], ["資產", "資產", "資產"],
            ["分部資產", "", ""], ["勘探業務", "40", "30"],
            ["煉油業務", "60", "50"], ["合計分部資產", "100", "80"],
        ],
    }
    output, decision = build_default_pipeline().run(
        table_refs_from_sources([source]),
        context={"prior_product_names": ["勘探業務", "煉油業務"]},
    )
    assert output == []
    assert "explicit_non_revenue_measure" in decision.classifications[0].reasons


def test_weak_segment_title_is_eligible_from_current_revenue_metric_columns():
    source = {
        "page_number": 9,
        "title": "分部資料（續）",
        "target_table": [
            ["", "分部收入", "分部收入", "分部業績", "分部業績"],
            ["", "2025年", "2024年", "2025年", "2024年"],
            ["健康業務", "40", "30", "8", "6"],
            ["數據業務", "60", "50", "12", "10"],
            ["總計", "100", "80", "20", "16"],
        ],
    }
    output, decision = build_default_pipeline().run(
        table_refs_from_sources([source]),
        context={"prior_product_names": ["健康業務", "數據業務"]},
    )
    assert "revenue_metric_column" in decision.all_evidence[0].layout_signals
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("健康業務", 40.0), ("數據業務", 60.0), ("合计", 100.0),
    ]


def test_revenue_metric_with_parenthetical_basis_forms_column_matrix():
    output, decision = run({
        "page_number": 65,
        "title": "截至2025年按經營分部劃分的收入及業績",
        "target_table": [
            ["", "礦產金", "礦產銅", "合計"],
            ["收入（外部和分部收入）", "40", "60", "100"],
            ["銷售成本", "30", "45", "75"],
            ["礦山經營盈利", "10", "15", "25"],
        ],
    })
    assert decision.selected.classification.table_type == "segment_matrix_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("礦產金", 40.0), ("礦產銅", 60.0), ("合计", 100.0),
    ]


def test_revenue_cost_and_percent_columns_stay_multi_metric():
    output, decision = run({
        "page_number": 30,
        "title": "2025年按產品劃分的收入與成本分析",
        "target_table": [
            ["分產品", "營業收入", "營業成本", "毛利率", "收入同比"],
            ["天然氣", "40", "30", "25%", "5%"],
            ["風電", "60", "20", "66.7%", "8%"],
            ["合計", "100", "50", "50%", "6.8%"],
        ],
    })
    assert decision.selected.classification.table_type == "row_metric_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("天然氣", 40.0), ("風電", 60.0), ("合计", 100.0),
    ]


def test_segment_metric_columns_choose_external_revenue_basis():
    output, decision = run({
        "page_number": 10,
        "title": "2025年分部收入及溢利分析",
        "target_table": [
            ["", "分部收入", "分部收入", "分部收入", "分部溢利"],
            ["", "總額", "分部間註銷", "對外", ""],
            ["建築", "70", "(10)", "60", "8"],
            ["材料", "50", "(10)", "40", "6"],
            ["總計", "120", "(20)", "100", "14"],
        ],
    })
    assert decision.selected.classification.table_type == "row_metric_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("建築", 60.0), ("材料", 40.0), ("合计", 100.0),
    ]


def test_revenue_cost_title_does_not_qualify_an_expense_nature_table():
    output, decision = run({
        "page_number": 13,
        "title": "2025年收益成本按性質劃分的明細",
        "target_table": [
            ["", "2025年金額", "佔收益成本百分比"],
            ["渠道佣金", "70", "70%"], ["服務器費", "30", "30%"],
            ["合計", "100", "100%"],
        ],
    })
    assert output == []
    assert "cost_or_expense_disclosure" in decision.classifications[0].reasons


def test_failed_primary_plan_does_not_fall_through_to_another_revenue_table():
    sources = [
        {
            "page_number": 7,
            "title": "按產品劃分的收入明細",
            "target_table": [
                ["", "本期"], ["產品甲", "40"], ["產品乙", "60"], ["合計", "100"],
            ],
        },
        {
            "page_number": 20,
            "title": "2025年其他收入分析",
            "target_table": [
                ["", "2025年"], ["利息收入", "20"], ["租金收入", "30"], ["合計", "50"],
            ],
        },
    ]
    output, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={"prior_product_names": ["產品甲", "產品乙"]},
    )
    assert output == []
    assert decision.debug["planned_tables"][0] == "p7:0"
    assert [item.classification.evidence.table.table_id for item in decision.rejected] == ["p7:0"]


def test_multi_section_row_materializes_only_product_service_section():
    output, decision = run({
        "page_number": 4,
        "title": "客户合約收入分類",
        "target_table": [
            ["", "2025年千元", "2024年千元"],
            ["商品或服務種類", "", ""],
            ["商品銷售", "70", "60"], ["服務收入", "30", "20"], ["總計", "100", "80"],
            ["地區市場", "", ""],
            ["中國", "80", "70"], ["海外", "20", "10"], ["總計", "100", "80"],
            ["收入確認時間", "", ""], ["某一時間點", "100", "80"],
        ],
    })
    assert decision.selected.classification.table_type == "multi_section_row"
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("商品銷售", 70.0), ("服務收入", 30.0), ("合计", 100.0),
    ]


def test_headerless_detail_uses_announcement_period_not_prior_amounts():
    sources = [{
        "page_number": 4,
        "title": "香港財務報告準則第15號範圍內的客户合約收益",
        "target_table": [
            ["於某一時點", "", ""], ["藥物銷售", "40", "30"],
            ["隨時間", "", ""], ["許可收入", "60", "50"], ["", "100", "80"],
        ],
    }]
    output, _ = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={
            "document_period_text": "截至2025年12月31日止年度",
            "prior_product_names": ["藥物銷售", "許可收入"],
        },
    )
    assert [(row["product_name"], row["mbrevenue"]) for row in output
            if row["end_date"] == "2025-12-31"] == [
        ("藥物銷售", 40.0), ("許可收入", 60.0), ("合计", 100.0),
    ]


def test_external_sales_row_with_product_columns_is_a_segment_matrix():
    sources = [{
        "page_number": 4,
        "title": "按可報告分部劃分之收益及業績分析",
        "target_table": [
            ["", "化學藥品千元", "生物藥品千元", "綜合千元"],
            ["外部銷售", "40", "60", "100"],
            ["分部溢利", "5", "8", "13"],
        ],
    }]
    output, decision = build_default_pipeline().run(
        table_refs_from_sources(sources),
        context={"document_period_text": "截至2025年12月31日止年度"},
    )
    assert decision.selected.classification.table_type == "segment_matrix_period"
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("化學藥品", 40.0), ("生物藥品", 60.0), ("合计", 100.0),
    ]


def test_financial_statement_with_explicit_product_revenue_section_is_supported():
    output, decision = run({
        "page_number": 2,
        "title": "未經審核綜合利潤表",
        "target_table": [
            ["", "2025年千元"], ["產品甲", "40"], ["產品乙", "60"],
            ["收入合計", "100"], ["營業成本", "(70)"], ["毛利", "30"],
        ],
    })
    assert [(row["product_name"], row["mbrevenue"]) for row in output] == [
        ("產品甲", 40.0), ("產品乙", 60.0), ("合计", 100.0),
    ]
