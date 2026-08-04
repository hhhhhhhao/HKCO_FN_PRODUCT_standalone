# HKCO_FN_PRODUCT 粗分类

公告数：1062；表格数：49648。

分类完全基于公告表格结构；错误数据如有，仅在分类完成后聚合。

## 第一层：直接排除或保留

| 决策 | 表格数 | 公告数 |
|---|---:|---:|
| exclude | 43360 | 1061 |
| retain_revenue_shape_rejected | 3266 | 947 |
| retain_primary_candidate | 2528 | 940 |
| retain_explicit_metric | 494 | 257 |

## 第二层：仅保留表的结构类


| 排名 | 表格数 | 公告数 | 可作主收入候选 | 类别 |
|---:|---:|---:|---:|---|
| 1 | 956 | 495 | 0 | retain_revenue_shape_rejected / generic_revenue / row_period |
| 2 | 760 | 482 | 760 | retain_primary_candidate / primary_revenue_detail / row_period |
| 3 | 393 | 271 | 0 | retain_revenue_shape_rejected / primary_revenue_detail / row_period |
| 4 | 389 | 285 | 0 | retain_revenue_shape_rejected / geography_revenue / row_period |
| 5 | 337 | 260 | 337 | retain_primary_candidate / segment_revenue / segment_matrix_period |
| 6 | 267 | 182 | 0 | retain_revenue_shape_rejected / generic_revenue / unsupported |
| 7 | 266 | 220 | 0 | retain_revenue_shape_rejected / generic_revenue / segment_matrix_period |
| 8 | 258 | 188 | 0 | retain_revenue_shape_rejected / primary_revenue_detail / unsupported |
| 9 | 249 | 193 | 249 | retain_primary_candidate / generic_revenue / row_period |
| 10 | 245 | 207 | 245 | retain_primary_candidate / generic_revenue / segment_matrix_period |
| 11 | 219 | 167 | 219 | retain_primary_candidate / segment_revenue / row_period |
| 12 | 126 | 112 | 126 | retain_primary_candidate / primary_revenue_detail / mixed_hierarchy |
| 13 | 109 | 104 | 0 | retain_revenue_shape_rejected / geography_revenue / row_metric_period |
| 14 | 109 | 82 | 109 | retain_primary_candidate / primary_revenue_detail / row_metric_period |
| 15 | 105 | 82 | 0 | retain_explicit_metric / explicit_cost / row_period |
| 16 | 97 | 54 | 77 | retain_explicit_metric / revenue_with_metrics / row_metric_period |
| 17 | 86 | 74 | 0 | retain_revenue_shape_rejected / generic_revenue / mixed_hierarchy |
| 18 | 77 | 72 | 77 | retain_primary_candidate / primary_revenue_detail / multi_section_row |
| 19 | 74 | 68 | 0 | retain_explicit_metric / explicit_gross_profit / row_metric_period |
| 20 | 67 | 51 | 0 | retain_revenue_shape_rejected / segment_revenue / row_period |
| 21 | 59 | 49 | 59 | retain_primary_candidate / segment_revenue / row_identity_total_period |
| 22 | 55 | 46 | 0 | retain_revenue_shape_rejected / segment_revenue / segment_matrix_period |
| 23 | 55 | 45 | 55 | retain_primary_candidate / primary_revenue_detail / segment_matrix_period |
| 24 | 51 | 47 | 0 | retain_revenue_shape_rejected / geography_revenue / unsupported |
| 25 | 50 | 42 | 50 | retain_primary_candidate / generic_revenue / mixed_hierarchy |
| 26 | 50 | 32 | 7 | retain_explicit_metric / revenue_with_metrics / row_period |
| 27 | 48 | 40 | 48 | retain_primary_candidate / generic_revenue / multi_section_row |
| 28 | 47 | 44 | 0 | retain_revenue_shape_rejected / primary_revenue_detail / multi_section_row |
| 29 | 45 | 38 | 0 | retain_revenue_shape_rejected / generic_revenue / multi_section_row |
| 30 | 40 | 34 | 0 | retain_explicit_metric / explicit_gross_profit / row_period |
| 31 | 38 | 30 | 0 | retain_revenue_shape_rejected / primary_revenue_detail / row_metric_period |
| 32 | 35 | 33 | 35 | retain_primary_candidate / segment_revenue / row_metric_period |
| 33 | 33 | 28 | 33 | retain_primary_candidate / primary_revenue_detail / row_identity_total_period |
| 34 | 32 | 27 | 0 | retain_revenue_shape_rejected / segment_revenue / unsupported |
| 35 | 31 | 29 | 0 | retain_revenue_shape_rejected / primary_revenue_detail / mixed_hierarchy |
| 36 | 31 | 26 | 0 | retain_revenue_shape_rejected / generic_revenue / row_metric_period |
| 37 | 27 | 24 | 0 | retain_explicit_metric / explicit_cost / mixed_hierarchy |
| 38 | 24 | 21 | 24 | retain_primary_candidate / generic_revenue / row_identity_total_period |
| 39 | 23 | 21 | 23 | retain_primary_candidate / generic_revenue / row_metric_period |
| 40 | 23 | 21 | 0 | retain_revenue_shape_rejected / generic_revenue / row_identity_total_period |

## 收入结构类

| 排名 | 表格数 | 公告数 | 类别 |
|---:|---:|---:|---|
| 1 | 789 | 425 | generic_revenue / row_period / unknown / rejected |
| 2 | 641 | 429 | primary_revenue_detail / row_period / product_service / eligible |
| 3 | 385 | 282 | geography_revenue / row_period / geography / rejected |
| 4 | 328 | 253 | segment_revenue / segment_matrix_period / business / eligible |
| 5 | 264 | 185 | primary_revenue_detail / row_period / product_service / rejected |
| 6 | 231 | 162 | generic_revenue / unsupported / unknown / rejected |
| 7 | 224 | 190 | generic_revenue / segment_matrix_period / unknown / rejected |
| 8 | 190 | 155 | generic_revenue / row_period / product_service / eligible |
| 9 | 161 | 140 | generic_revenue / segment_matrix_period / business / eligible |
| 10 | 152 | 112 | segment_revenue / row_period / business / eligible |
| 11 | 122 | 96 | primary_revenue_detail / unsupported / product_service / rejected |
| 12 | 119 | 93 | primary_revenue_detail / row_period / business / eligible |
| 13 | 110 | 98 | primary_revenue_detail / mixed_hierarchy / product_service / eligible |
| 14 | 109 | 104 | geography_revenue / row_metric_period / geography / rejected |
| 15 | 84 | 73 | generic_revenue / segment_matrix_period / product_service / eligible |
| 16 | 81 | 69 | primary_revenue_detail / unsupported / unknown / rejected |
| 17 | 77 | 72 | primary_revenue_detail / multi_section_row / product_service / eligible |
| 18 | 77 | 60 | primary_revenue_detail / row_metric_period / product_service / eligible |
| 19 | 72 | 55 | generic_revenue / row_period / geography / rejected |
| 20 | 67 | 62 | segment_revenue / row_period / product_service / eligible |
| 21 | 60 | 49 | generic_revenue / mixed_hierarchy / unknown / rejected |
| 22 | 59 | 47 | generic_revenue / row_period / business / eligible |
| 23 | 56 | 31 | revenue_with_metrics / row_metric_period / business / eligible |
| 24 | 51 | 47 | geography_revenue / unsupported / geography / rejected |
| 25 | 50 | 45 | generic_revenue / row_period / product_service / rejected |
| 26 | 48 | 40 | generic_revenue / multi_section_row / product_service / eligible |
| 27 | 45 | 42 | primary_revenue_detail / row_period / geography / rejected |
| 28 | 44 | 36 | primary_revenue_detail / unsupported / business / rejected |
| 29 | 38 | 37 | segment_revenue / row_identity_total_period / business / eligible |
| 30 | 37 | 32 | generic_revenue / mixed_hierarchy / product_service / eligible |
| 31 | 35 | 27 | segment_revenue / row_period / business / rejected |
| 32 | 32 | 28 | primary_revenue_detail / row_metric_period / business / eligible |
| 33 | 32 | 26 | primary_revenue_detail / row_period / recognition_time / rejected |
| 34 | 31 | 30 | generic_revenue / row_period / business / rejected |
| 35 | 29 | 23 | primary_revenue_detail / segment_matrix_period / business / eligible |
| 36 | 28 | 24 | segment_revenue / unsupported / business / rejected |
| 37 | 26 | 22 | primary_revenue_detail / segment_matrix_period / product_service / eligible |
| 38 | 24 | 24 | generic_revenue / unsupported / product_service / rejected |
| 39 | 24 | 23 | segment_revenue / row_metric_period / business / eligible |
| 40 | 24 | 21 | primary_revenue_detail / multi_section_row / product_service / rejected |
| 41 | 23 | 19 | primary_revenue_detail / row_identity_total_period / product_service / eligible |
| 42 | 22 | 16 | segment_revenue / segment_matrix_period / geography / rejected |
| 43 | 21 | 20 | segment_revenue / segment_matrix_period / business / rejected |
| 44 | 21 | 17 | generic_revenue / row_metric_period / unknown / rejected |
| 45 | 21 | 17 | revenue_with_metrics / row_metric_period / product_service / eligible |
| 46 | 21 | 15 | segment_revenue / row_period / geography / rejected |
| 47 | 21 | 12 | segment_revenue / row_identity_total_period / product_service / eligible |
| 48 | 20 | 19 | primary_revenue_detail / row_period / business / rejected |
| 49 | 20 | 18 | primary_revenue_detail / mixed_hierarchy / product_service / rejected |
| 50 | 19 | 19 | generic_revenue / mixed_hierarchy / product_service / rejected |
| 51 | 19 | 17 | generic_revenue / row_metric_period / product_service / eligible |
| 52 | 16 | 15 | generic_revenue / segment_matrix_period / geography / rejected |
| 53 | 16 | 15 | primary_revenue_detail / mixed_hierarchy / business / eligible |
| 54 | 16 | 13 | segment_revenue / mixed_hierarchy / business / eligible |
| 55 | 15 | 15 | generic_revenue / segment_matrix_period / product_service / rejected |
| 56 | 15 | 14 | geography_revenue / mixed_hierarchy / geography / rejected |
| 57 | 15 | 12 | primary_revenue_detail / row_period / sales_channel / rejected |
| 58 | 14 | 13 | generic_revenue / row_identity_total_period / product_service / eligible |
| 59 | 14 | 13 | geography_revenue / row_period / product_service / eligible |
| 60 | 13 | 11 | primary_revenue_detail / row_measurement_period / product_service / eligible |
| 61 | 13 | 10 | generic_revenue / mixed_hierarchy / business / eligible |
| 62 | 13 | 10 | revenue_with_metrics / row_period / product_service / rejected |
| 63 | 12 | 12 | generic_revenue / multi_section_row / product_service / rejected |
| 64 | 12 | 12 | generic_revenue / row_identity_total_period / unknown / rejected |
| 65 | 12 | 11 | generic_revenue / multi_section_row / unknown / rejected |
| 66 | 12 | 10 | geography_revenue / row_measurement_period / geography / rejected |
| 67 | 12 | 9 | primary_revenue_detail / row_metric_period / sales_channel / rejected |
| 68 | 12 | 7 | primary_revenue_detail / segment_matrix_period / unknown / rejected |
| 69 | 11 | 11 | primary_revenue_detail / multi_section_row / geography / rejected |
| 70 | 11 | 11 | primary_revenue_detail / row_metric_period / geography / rejected |
| 71 | 11 | 10 | primary_revenue_detail / row_period / unknown / rejected |
| 72 | 11 | 10 | segment_revenue / row_metric_period / product_service / eligible |
| 73 | 11 | 9 | revenue_with_metrics / row_period / business / rejected |
| 74 | 11 | 9 | revenue_with_metrics / row_period / geography / rejected |
| 75 | 11 | 7 | revenue_with_metrics / unsupported / unknown / rejected |
| 76 | 10 | 9 | primary_revenue_detail / row_identity_total_period / business / eligible |
| 77 | 10 | 8 | generic_revenue / row_identity_total_period / business / eligible |
| 78 | 10 | 8 | segment_revenue / row_metric_period / geography / rejected |
| 79 | 9 | 8 | generic_revenue / row_period / recognition_time / rejected |
| 80 | 9 | 8 | geography_revenue / segment_matrix_period / geography / rejected |
| 81 | 9 | 7 | generic_revenue / row_identity_total_period / geography / rejected |
| 82 | 9 | 7 | segment_revenue / segment_matrix_period / product_service / eligible |
| 83 | 9 | 6 | revenue_with_metrics / multi_section_row / product_service / eligible |
| 84 | 8 | 8 | generic_revenue / multi_section_row / geography / rejected |
| 85 | 8 | 8 | generic_revenue / unsupported / business / rejected |
| 86 | 8 | 7 | geography_revenue / row_identity_total_period / geography / rejected |
| 87 | 8 | 6 | generic_revenue / multi_section_row / business / rejected |
| 88 | 8 | 6 | segment_revenue / segment_matrix_period / recognition_time / rejected |
| 89 | 7 | 7 | generic_revenue / row_metric_period / geography / rejected |
| 90 | 7 | 7 | generic_revenue / segment_matrix_period / business / rejected |
| 91 | 7 | 7 | revenue_with_metrics / segment_matrix_period / business / eligible |
| 92 | 7 | 6 | revenue_with_metrics / row_period / unknown / rejected |
| 93 | 7 | 5 | revenue_with_metrics / row_metric_period / product_service / rejected |
| 94 | 6 | 6 | generic_revenue / mixed_hierarchy / business / rejected |
| 95 | 6 | 6 | primary_revenue_detail / multi_section_row / recognition_time / rejected |
| 96 | 6 | 6 | primary_revenue_detail / unsupported / recognition_time / rejected |
| 97 | 6 | 6 | segment_revenue / mixed_hierarchy / product_service / eligible |
| 98 | 6 | 6 | segment_revenue / row_period / product_service / rejected |
| 99 | 6 | 5 | segment_revenue / multi_section_row / product_service / eligible |
| 100 | 6 | 4 | revenue_with_metrics / mixed_hierarchy / product_service / rejected |
| 101 | 6 | 4 | revenue_with_metrics / row_period / business / eligible |
| 102 | 6 | 3 | primary_revenue_detail / row_metric_period / business / rejected |
| 103 | 6 | 3 | primary_revenue_detail / row_period / measurement_method / rejected |
| 104 | 5 | 5 | generic_revenue / row_period / sales_channel / rejected |
| 105 | 5 | 5 | geography_revenue / multi_section_row / geography / rejected |
| 106 | 5 | 4 | geography_revenue / multi_section_row / product_service / eligible |
| 107 | 5 | 4 | segment_revenue / row_period / recognition_time / rejected |
| 108 | 5 | 3 | primary_revenue_detail / segment_matrix_period / geography / rejected |
| 109 | 4 | 4 | generic_revenue / row_measurement_period / product_service / eligible |
| 110 | 4 | 4 | generic_revenue / row_metric_period / business / eligible |
| 111 | 4 | 4 | geography_revenue / row_period / business / eligible |
| 112 | 4 | 4 | primary_revenue_detail / mixed_hierarchy / geography / rejected |
| 113 | 4 | 4 | primary_revenue_detail / multi_section_row / business / rejected |
| 114 | 4 | 4 | primary_revenue_detail / row_metric_period / product_service / rejected |
| 115 | 4 | 4 | primary_revenue_detail / row_metric_period / unknown / rejected |
| 116 | 4 | 4 | segment_revenue / segment_matrix_period / sales_channel / rejected |
| 117 | 4 | 3 | generic_revenue / unsupported / geography / rejected |
| 118 | 4 | 3 | revenue_with_metrics / row_metric_period / geography / rejected |
| 119 | 4 | 3 | revenue_with_metrics / unsupported / product_service / rejected |
| 120 | 4 | 2 | revenue_with_metrics / row_metric_period / recognition_time / rejected |
| 121 | 3 | 3 | generic_revenue / segment_matrix_period / sales_channel / rejected |
| 122 | 3 | 3 | geography_revenue / mixed_hierarchy / product_service / eligible |
| 123 | 3 | 3 | geography_revenue / row_period / product_service / rejected |
| 124 | 3 | 3 | primary_revenue_detail / row_identity_total_period / geography / rejected |
| 125 | 3 | 3 | primary_revenue_detail / row_identity_total_period / recognition_time / rejected |
| 126 | 3 | 3 | primary_revenue_detail / segment_matrix_period / product_service / rejected |
| 127 | 3 | 3 | primary_revenue_detail / unsupported / sales_channel / rejected |
| 128 | 3 | 3 | revenue_with_metrics / segment_matrix_period / business / rejected |
| 129 | 3 | 3 | revenue_with_metrics / unsupported / business / rejected |
| 130 | 3 | 3 | segment_revenue / mixed_hierarchy / business / rejected |
| 131 | 3 | 3 | segment_revenue / multi_section_row / business / rejected |
| 132 | 3 | 3 | segment_revenue / multi_section_row / geography / rejected |
| 133 | 3 | 3 | segment_revenue / unsupported / product_service / rejected |
| 134 | 3 | 2 | segment_revenue / multi_section_row / product_service / rejected |
| 135 | 2 | 2 | generic_revenue / multi_section_row / customer / rejected |
| 136 | 2 | 2 | generic_revenue / multi_section_row / recognition_time / rejected |
| 137 | 2 | 2 | generic_revenue / row_metric_period / business / rejected |
| 138 | 2 | 2 | geography_revenue / mixed_hierarchy / product_service / rejected |
| 139 | 2 | 2 | primary_revenue_detail / mixed_hierarchy / business / rejected |
| 140 | 2 | 2 | primary_revenue_detail / mixed_hierarchy / sales_channel / rejected |
| 141 | 2 | 2 | primary_revenue_detail / mixed_hierarchy / unknown / rejected |
| 142 | 2 | 2 | primary_revenue_detail / row_identity_total_period / sales_channel / rejected |
| 143 | 2 | 2 | primary_revenue_detail / row_measurement_period / business / eligible |
| 144 | 2 | 2 | primary_revenue_detail / segment_matrix_period / business / rejected |
| 145 | 2 | 2 | primary_revenue_detail / unsupported / geography / rejected |
| 146 | 2 | 2 | product_service_breakdown / unsupported / product_service / rejected |
| 147 | 2 | 2 | revenue_with_metrics / multi_section_row / geography / rejected |
| 148 | 2 | 2 | revenue_with_metrics / row_metric_period / business / rejected |
| 149 | 2 | 2 | revenue_with_metrics / row_metric_period / unknown / rejected |
| 150 | 2 | 2 | segment_revenue / multi_section_row / recognition_time / rejected |
| 151 | 2 | 2 | segment_revenue / row_identity_total_period / business / rejected |
| 152 | 2 | 2 | segment_revenue / row_metric_period / business / rejected |
| 153 | 2 | 1 | revenue_with_metrics / multi_section_row / product_service / rejected |
| 154 | 2 | 1 | segment_revenue / mixed_hierarchy / sales_channel / rejected |
| 155 | 1 | 1 | generic_revenue / mixed_hierarchy / sales_channel / rejected |
| 156 | 1 | 1 | generic_revenue / multi_section_row / sales_channel / rejected |
| 157 | 1 | 1 | generic_revenue / row_identity_total_period / product_service / rejected |
| 158 | 1 | 1 | generic_revenue / row_identity_total_period / recognition_time / rejected |
| 159 | 1 | 1 | generic_revenue / row_metric_period / product_service / rejected |
| 160 | 1 | 1 | generic_revenue / segment_matrix_period / customer / rejected |
| 161 | 1 | 1 | geography_revenue / multi_section_row / product_service / rejected |
| 162 | 1 | 1 | geography_revenue / row_identity_total_period / business / eligible |
| 163 | 1 | 1 | geography_revenue / row_metric_period / business / eligible |
| 164 | 1 | 1 | geography_revenue / row_period / recognition_time / rejected |
| 165 | 1 | 1 | geography_revenue / segment_matrix_period / business / eligible |
| 166 | 1 | 1 | primary_revenue_detail / mixed_hierarchy / recognition_time / rejected |
| 167 | 1 | 1 | primary_revenue_detail / multi_section_row / sales_channel / rejected |
| 168 | 1 | 1 | primary_revenue_detail / multi_section_row / unknown / rejected |
| 169 | 1 | 1 | primary_revenue_detail / row_identity_total_period / business / rejected |
| 170 | 1 | 1 | primary_revenue_detail / row_identity_total_period / measurement_method / rejected |
| 171 | 1 | 1 | primary_revenue_detail / row_metric_period / recognition_time / rejected |
| 172 | 1 | 1 | primary_revenue_detail / segment_matrix_period / sales_channel / rejected |
| 173 | 1 | 1 | product_service_breakdown / multi_section_row / product_service / eligible |
| 174 | 1 | 1 | product_service_breakdown / multi_section_row / product_service / rejected |
| 175 | 1 | 1 | product_service_breakdown / row_identity_total_period / product_service / eligible |
| 176 | 1 | 1 | product_service_breakdown / row_period / product_service / rejected |
| 177 | 1 | 1 | revenue_with_metrics / row_identity_total_period / product_service / eligible |
| 178 | 1 | 1 | revenue_with_metrics / row_identity_total_period / recognition_time / rejected |
| 179 | 1 | 1 | revenue_with_metrics / row_measurement_period / product_service / eligible |
| 180 | 1 | 1 | revenue_with_metrics / row_metric_period / sales_channel / rejected |
| 181 | 1 | 1 | revenue_with_metrics / row_period / product_service / eligible |
| 182 | 1 | 1 | revenue_with_metrics / row_period / sales_channel / rejected |
| 183 | 1 | 1 | revenue_with_metrics / segment_matrix_period / geography / rejected |
| 184 | 1 | 1 | revenue_with_metrics / segment_matrix_period / product_service / eligible |
| 185 | 1 | 1 | revenue_with_metrics / segment_matrix_period / product_service / rejected |
| 186 | 1 | 1 | revenue_with_metrics / segment_matrix_period / unknown / rejected |
| 187 | 1 | 1 | revenue_with_metrics / unsupported / geography / rejected |
| 188 | 1 | 1 | segment_revenue / mixed_hierarchy / geography / rejected |
| 189 | 1 | 1 | segment_revenue / row_identity_total_period / recognition_time / rejected |
| 190 | 1 | 1 | segment_revenue / row_measurement_period / product_service / eligible |
| 191 | 1 | 1 | segment_revenue / row_metric_period / product_service / rejected |
| 192 | 1 | 1 | segment_revenue / unsupported / geography / rejected |

## 公告收入计划类别

| 排名 | 公告数 | Missing | Extra | Value diff | 总错误 | 类别 |
|---:|---:|---:|---:|---:|---:|---|
| 1 | 358 | 955 | 416 | 225 | 1596 | multi_axis / business+product_service |
| 2 | 27 | 6 | 4 | 0 | 10 | single / row_period / business |
| 3 | 93 | 3 | 6 | 0 | 9 | single / row_period / product_service |
| 4 | 87 | 0 | 4 | 4 | 8 | multiple_mixed_geometry / product_service |
| 5 | 19 | 8 | 0 | 0 | 8 | same_title_family / product_service |
| 6 | 27 | 6 | 0 | 0 | 6 | same_title_family / business |
| 7 | 71 | 4 | 0 | 0 | 4 | period_sibling_family / product_service |
| 8 | 10 | 0 | 1 | 1 | 2 | multiple_same_geometry / row_period / business |
| 9 | 95 | 0 | 0 | 0 | 0 | revenue_shaped_but_all_rejected |
| 10 | 38 | 0 | 0 | 0 | 0 | multiple_same_geometry / row_period / product_service |
| 11 | 33 | 0 | 0 | 0 | 0 | multiple_mixed_geometry / business |
| 12 | 27 | 0 | 0 | 0 | 0 | single / segment_matrix_period / business |
| 13 | 25 | 0 | 0 | 0 | 0 | multiple_same_geometry / segment_matrix_period / business |
| 14 | 20 | 0 | 0 | 0 | 0 | continuation_family / business |
| 15 | 20 | 0 | 0 | 0 | 0 | single / mixed_hierarchy / product_service |
| 16 | 19 | 0 | 0 | 0 | 0 | no_revenue_shaped_table |
| 17 | 17 | 0 | 0 | 0 | 0 | continuation_family / product_service |
| 18 | 12 | 0 | 0 | 0 | 0 | single / segment_matrix_period / product_service |
| 19 | 10 | 0 | 0 | 0 | 0 | single / row_metric_period / product_service |
| 20 | 9 | 0 | 0 | 0 | 0 | period_sibling_family / business |
| 21 | 9 | 0 | 0 | 0 | 0 | single / multi_section_row / product_service |
| 22 | 9 | 0 | 0 | 0 | 0 | single / row_identity_total_period / business |
| 23 | 6 | 0 | 0 | 0 | 0 | multiple_same_geometry / row_metric_period / product_service |
| 24 | 5 | 0 | 0 | 0 | 0 | single / mixed_hierarchy / business |
| 25 | 5 | 0 | 0 | 0 | 0 | single / row_metric_period / business |
| 26 | 3 | 0 | 0 | 0 | 0 | single / row_identity_total_period / product_service |
| 27 | 2 | 0 | 0 | 0 | 0 | multiple_same_geometry / multi_section_row / product_service |
| 28 | 2 | 0 | 0 | 0 | 0 | single / row_measurement_period / product_service |
| 29 | 1 | 0 | 0 | 0 | 0 | multiple_same_geometry / mixed_hierarchy / product_service |
| 30 | 1 | 0 | 0 | 0 | 0 | multiple_same_geometry / row_identity_total_period / product_service |
| 31 | 1 | 0 | 0 | 0 | 0 | multiple_same_geometry / row_metric_period / business |
| 32 | 1 | 0 | 0 | 0 | 0 | multiple_same_geometry / segment_matrix_period / product_service |
