#!/usr/bin/env python3
"""
NidanEHR Superset — Configurable & Filterable Hospital Analytics
================================================================

Design goals (see README.md "Reporting architecture"):

1. WIDE FACT DATASETS, not pre-aggregated reports.
   Each dataset is one row per business event (visit, encounter, diagnosis,
   lab order, invoice line, ...) carrying:
     - a single real datetime column  -> enables Day/Week/Month/Quarter/Year roll-up
     - every dimension preserved      -> enables data-element filtering
     - additive base measures + SAVED metrics for distinct counts
   No `WHERE date >= NOW()-90d` windows and no `LIMIT` are baked into the SQL,
   so dashboard filters (custom range, "this year", etc.) actually work.

2. UNIVERSAL FILTERS on every dashboard, generated programmatically:
     - Time Range   (presets + custom range)         -> filter_time
     - Time Grain   (Day/Week/Month/Quarter/Year)    -> filter_timegrain
     - one value filter per declared data element     -> filter_select
   plus a real grid layout (position_json) and cross-filtering.

Dialects: OpenMRS = MySQL/MariaDB (this branch), OpenELIS & Odoo = PostgreSQL.
Each dataset's SQL is written for its source dialect. When OpenMRS migrates to
PostgreSQL (feat/postgres), set OPENMRS_DB_TYPE=postgresql AND port the OpenMRS
SQL below (DATE_SUB/TIMESTAMPDIFF/CURDATE/HOUR -> Postgres equivalents).

The script is idempotent: it creates missing objects and updates SQL / metrics /
chart params / dashboard filters in place on every container start.
"""

import hashlib
import json
import os
import sys
from urllib.parse import quote_plus

sys.path.insert(0, "/app")


# --------------------------------------------------------------------------- #
# Connection helpers
# --------------------------------------------------------------------------- #
def get_db_uri(prefix: str, default_host: str, db_type: str = "postgresql") -> str:
    """Build a SQLAlchemy URI from env vars. db_type: postgresql | mysql."""
    host = os.environ.get(f"{prefix}_HOST", default_host)
    port = os.environ.get(f"{prefix}_PORT", "5432" if db_type == "postgresql" else "3306")
    name = os.environ.get(f"{prefix}_NAME", "")
    user = os.environ.get(f"{prefix}_USER", "")
    password = os.environ.get(f"{prefix}_PASSWORD", "")
    safe_password = quote_plus(password) if password else ""
    if db_type == "mysql":
        return f"mysql+pymysql://{user}:{safe_password}@{host}:{port}/{name}"
    return f"postgresql://{user}:{safe_password}@{host}:{port}/{name}"


# --------------------------------------------------------------------------- #
# Reusable SQL fragments
# --------------------------------------------------------------------------- #
# HMIS-style age bands. `bd` = birthdate column, `ref` = reference date expr.
def age_group_mysql(bd: str, ref: str) -> str:
    return f"""CASE
        WHEN TIMESTAMPDIFF(YEAR, {bd}, {ref}) < 1 THEN '<1'
        WHEN TIMESTAMPDIFF(YEAR, {bd}, {ref}) BETWEEN 1 AND 4 THEN '01-04'
        WHEN TIMESTAMPDIFF(YEAR, {bd}, {ref}) BETWEEN 5 AND 14 THEN '05-14'
        WHEN TIMESTAMPDIFF(YEAR, {bd}, {ref}) BETWEEN 15 AND 24 THEN '15-24'
        WHEN TIMESTAMPDIFF(YEAR, {bd}, {ref}) BETWEEN 25 AND 34 THEN '25-34'
        WHEN TIMESTAMPDIFF(YEAR, {bd}, {ref}) BETWEEN 35 AND 44 THEN '35-44'
        WHEN TIMESTAMPDIFF(YEAR, {bd}, {ref}) BETWEEN 45 AND 54 THEN '45-54'
        WHEN TIMESTAMPDIFF(YEAR, {bd}, {ref}) BETWEEN 55 AND 64 THEN '55-64'
        ELSE '65+' END"""


def age_group_pg(years_expr: str) -> str:
    return f"""CASE
        WHEN {years_expr} < 1 THEN '<1'
        WHEN {years_expr} BETWEEN 1 AND 4 THEN '01-04'
        WHEN {years_expr} BETWEEN 5 AND 14 THEN '05-14'
        WHEN {years_expr} BETWEEN 15 AND 24 THEN '15-24'
        WHEN {years_expr} BETWEEN 25 AND 34 THEN '25-34'
        WHEN {years_expr} BETWEEN 35 AND 44 THEN '35-44'
        WHEN {years_expr} BETWEEN 45 AND 54 THEN '45-54'
        WHEN {years_expr} BETWEEN 55 AND 64 THEN '55-64'
        ELSE '65+' END"""


# --------------------------------------------------------------------------- #
# DATASET SPECS
# Each: key (source db), table_name, sql, dttm (main datetime col or None),
#       columns [(name, type, is_dim)], metrics [(name, expression)]
# `is_dim` columns are made groupby+filterable so they appear as filter options.
# --------------------------------------------------------------------------- #
def build_dataset_specs():
    specs = []

    # ---- OpenMRS: PATIENTS (registration grain) --------------------------- #
    specs.append({
        "db": "openmrs", "table_name": "fct_patients", "dttm": "registered_at",
        "sql": f"""
            SELECT
                p.patient_id,
                p.date_created AS registered_at,
                COALESCE(pe.gender, 'U') AS gender,
                {age_group_mysql('pe.birthdate', 'CURDATE()')} AS age_group,
                COALESCE(pa.city_village, 'Unknown') AS municipality,
                COALESCE(pa.county_district, 'Unknown') AS district,
                COALESCE(pa.state_province, 'Unknown') AS province
            FROM patient p
            JOIN person pe ON p.patient_id = pe.person_id
            LEFT JOIN (
                SELECT person_id, city_village, county_district, state_province,
                       ROW_NUMBER() OVER (PARTITION BY person_id
                           ORDER BY preferred DESC, person_address_id DESC) AS rn
                FROM person_address WHERE voided = 0
            ) pa ON pa.person_id = pe.person_id AND pa.rn = 1
            WHERE p.voided = 0
        """,
        "columns": [
            ("patient_id", "BIGINT", False), ("registered_at", "TIMESTAMP", False),
            ("gender", "VARCHAR", True), ("age_group", "VARCHAR", True),
            ("municipality", "VARCHAR", True), ("district", "VARCHAR", True),
            ("province", "VARCHAR", True),
        ],
        "metrics": [("Patients", "COUNT(DISTINCT patient_id)")],
    })

    # ---- OpenMRS: VISITS -------------------------------------------------- #
    specs.append({
        "db": "openmrs", "table_name": "fct_visits", "dttm": "started_at",
        "sql": f"""
            SELECT
                v.visit_id, v.patient_id,
                v.date_started AS started_at,
                v.date_stopped AS ended_at,
                HOUR(v.date_started) AS hour_of_day,
                COALESCE(vt.name, 'Unknown') AS visit_type,
                COALESCE(loc.name, 'Unknown') AS location,
                COALESCE(pe.gender, 'U') AS gender,
                {age_group_mysql('pe.birthdate', 'v.date_started')} AS age_group,
                CASE WHEN v.date_stopped IS NULL THEN 'Open' ELSE 'Closed' END AS visit_status,
                CASE WHEN v.date_stopped IS NOT NULL
                     THEN TIMESTAMPDIFF(SECOND, v.date_started, v.date_stopped) / 86400.0
                END AS los_days
            FROM visit v
            LEFT JOIN visit_type vt ON v.visit_type_id = vt.visit_type_id
            LEFT JOIN location loc ON v.location_id = loc.location_id
            JOIN person pe ON v.patient_id = pe.person_id
            WHERE v.voided = 0
        """,
        "columns": [
            ("visit_id", "BIGINT", False), ("patient_id", "BIGINT", False),
            ("started_at", "TIMESTAMP", False), ("ended_at", "TIMESTAMP", False),
            ("hour_of_day", "INTEGER", True), ("visit_type", "VARCHAR", True),
            ("location", "VARCHAR", True), ("gender", "VARCHAR", True),
            ("age_group", "VARCHAR", True), ("visit_status", "VARCHAR", True),
            ("los_days", "FLOAT", False),
        ],
        "metrics": [
            ("Visits", "COUNT(DISTINCT visit_id)"),
            ("Unique Patients", "COUNT(DISTINCT patient_id)"),
            ("Avg LOS (days)", "AVG(los_days)"),
        ],
    })

    # ---- OpenMRS: ENCOUNTERS (encounter x provider grain) ----------------- #
    specs.append({
        "db": "openmrs", "table_name": "fct_encounters", "dttm": "encounter_at",
        "sql": """
            SELECT
                e.encounter_id, e.patient_id,
                e.encounter_datetime AS encounter_at,
                COALESCE(et.name, 'Unknown') AS encounter_type,
                COALESCE(loc.name, 'Unknown') AS location,
                COALESCE(NULLIF(TRIM(CONCAT(COALESCE(pn.given_name, ''), ' ',
                         COALESCE(pn.family_name, ''))), ''), 'Unknown') AS provider_name
            FROM encounter e
            LEFT JOIN encounter_type et ON e.encounter_type = et.encounter_type_id
            LEFT JOIN location loc ON e.location_id = loc.location_id
            LEFT JOIN encounter_provider ep ON e.encounter_id = ep.encounter_id
                AND (ep.voided = 0 OR ep.voided IS NULL)
            LEFT JOIN provider p ON ep.provider_id = p.provider_id
            LEFT JOIN person_name pn ON p.person_id = pn.person_id AND pn.voided = 0
            WHERE e.voided = 0
        """,
        "columns": [
            ("encounter_id", "BIGINT", False), ("patient_id", "BIGINT", False),
            ("encounter_at", "TIMESTAMP", False), ("encounter_type", "VARCHAR", True),
            ("location", "VARCHAR", True), ("provider_name", "VARCHAR", True),
        ],
        "metrics": [
            ("Encounters", "COUNT(DISTINCT encounter_id)"),
            ("Patients", "COUNT(DISTINCT patient_id)"),
        ],
    })

    # ---- OpenMRS: DIAGNOSES (morbidity / surveillance) -------------------- #
    # Source: encounter_diagnosis (OpenMRS 2.x). May be empty if diagnoses are
    # captured only as obs in this deployment -> then this dataset returns 0 rows
    # (harmless). Gives age/sex-disaggregated morbidity for public-health reports.
    specs.append({
        "db": "openmrs", "table_name": "fct_diagnoses", "dttm": "diagnosed_at",
        "sql": f"""
            SELECT
                ed.diagnosis_id, ed.patient_id,
                ed.date_created AS diagnosed_at,
                COALESCE(cn.name, ed.diagnosis_non_coded, 'Unknown') AS diagnosis,
                CASE WHEN ed.dx_rank = 1 THEN 'Primary' ELSE 'Secondary' END AS dx_rank,
                COALESCE(ed.certainty, 'Unknown') AS certainty,
                COALESCE(pe.gender, 'U') AS gender,
                {age_group_mysql('pe.birthdate', 'ed.date_created')} AS age_group
            FROM encounter_diagnosis ed
            LEFT JOIN concept_name cn ON ed.diagnosis_coded = cn.concept_id
                AND cn.locale = 'en' AND cn.concept_name_type = 'FULLY_SPECIFIED'
                AND cn.voided = 0
            JOIN person pe ON ed.patient_id = pe.person_id
            WHERE ed.voided = 0
        """,
        "columns": [
            ("diagnosis_id", "BIGINT", False), ("patient_id", "BIGINT", False),
            ("diagnosed_at", "TIMESTAMP", False), ("diagnosis", "VARCHAR", True),
            ("dx_rank", "VARCHAR", True), ("certainty", "VARCHAR", True),
            ("gender", "VARCHAR", True), ("age_group", "VARCHAR", True),
        ],
        "metrics": [
            ("Diagnoses", "COUNT(*)"),
            ("Patients", "COUNT(DISTINCT patient_id)"),
        ],
    })

    # ---- OpenMRS: DRUG ORDERS (prescriptions) ----------------------------- #
    specs.append({
        "db": "openmrs", "table_name": "fct_drug_orders", "dttm": "ordered_at",
        "sql": """
            SELECT
                o.order_id, o.patient_id,
                o.date_activated AS ordered_at,
                COALESCE(cn.name, 'Unknown') AS drug_name,
                COALESCE(o.order_action, 'NEW') AS order_action
            FROM orders o
            JOIN drug_order dord ON o.order_id = dord.order_id
            LEFT JOIN drug d ON dord.drug_inventory_id = d.drug_id
            LEFT JOIN concept_name cn ON d.concept_id = cn.concept_id
                AND cn.locale = 'en' AND cn.concept_name_type = 'FULLY_SPECIFIED'
                AND cn.voided = 0
            WHERE o.voided = 0
        """,
        "columns": [
            ("order_id", "BIGINT", False), ("patient_id", "BIGINT", False),
            ("ordered_at", "TIMESTAMP", False), ("drug_name", "VARCHAR", True),
            ("order_action", "VARCHAR", True),
        ],
        "metrics": [
            ("Prescriptions", "COUNT(DISTINCT order_id)"),
            ("Patients", "COUNT(DISTINCT patient_id)"),
        ],
    })

    # ---- OpenMRS: BED OCCUPANCY (Bahmni bedmanagement) -------------------- #
    # One row per bed assignment. Schema is from the bedmanagement OMOD added on
    # this branch; column names may need tweaking against the live DB.
    specs.append({
        "db": "openmrs", "table_name": "fct_bed_assignments", "dttm": "assigned_at",
        "sql": """
            SELECT
                bpam.id AS assignment_id,
                bpam.patient_id,
                bpam.start_datetime AS assigned_at,
                bpam.end_datetime AS released_at,
                COALESCE(b.bed_number, 'Unknown') AS bed_number,
                COALESCE(bt.name, 'Unknown') AS bed_type,
                COALESCE(loc.name, 'Unknown') AS ward,
                CASE WHEN bpam.end_datetime IS NULL THEN 'Occupied' ELSE 'Released' END AS bed_status,
                TIMESTAMPDIFF(SECOND, bpam.start_datetime,
                    COALESCE(bpam.end_datetime, NOW())) / 86400.0 AS bed_days
            FROM bed_patient_assignment_map bpam
            JOIN bed b ON bpam.bed_id = b.bed_id
            LEFT JOIN bed_type bt ON b.bed_type_id = bt.bed_type_id
            LEFT JOIN bed_location_map blm ON b.bed_id = blm.bed_id
            LEFT JOIN location loc ON blm.location_id = loc.location_id
        """,
        "columns": [
            ("assignment_id", "BIGINT", False), ("patient_id", "BIGINT", False),
            ("assigned_at", "TIMESTAMP", False), ("released_at", "TIMESTAMP", False),
            ("bed_number", "VARCHAR", True), ("bed_type", "VARCHAR", True),
            ("ward", "VARCHAR", True), ("bed_status", "VARCHAR", True),
            ("bed_days", "FLOAT", False),
        ],
        "metrics": [
            ("Admissions", "COUNT(DISTINCT assignment_id)"),
            ("Bed Days", "SUM(bed_days)"),
            ("Avg Bed Days", "AVG(bed_days)"),
        ],
    })

    # ---- OpenELIS: LAB ORDERS (analysis grain) ---------------------------- #
    specs.append({
        "db": "openelis", "table_name": "fct_lab_orders", "dttm": "ordered_at",
        "sql": """
            SELECT
                a.id AS analysis_id,
                a.entry_date AS ordered_at,
                a.lastupdated AS updated_at,
                COALESCE(t.description, 'Unknown') AS test_name,
                COALESCE(ts.name, 'Unknown') AS test_section,
                COALESCE(sos.name, 'Unknown') AS status,
                sh.patient_id,
                EXTRACT(EPOCH FROM (COALESCE(a.lastupdated, a.entry_date) - a.entry_date)) / 3600.0 AS tat_hours
            FROM analysis a
            LEFT JOIN test t ON a.test_id = t.id
            LEFT JOIN test_section ts ON t.test_section_id = ts.id
            LEFT JOIN status_of_sample sos ON a.status_id = sos.id
            LEFT JOIN sample_item si ON a.sampitem_id = si.id
            LEFT JOIN sample s ON si.samp_id = s.id
            LEFT JOIN sample_human sh ON s.id = sh.samp_id
        """,
        "columns": [
            ("analysis_id", "BIGINT", False), ("ordered_at", "TIMESTAMP", False),
            ("updated_at", "TIMESTAMP", False), ("test_name", "VARCHAR", True),
            ("test_section", "VARCHAR", True), ("status", "VARCHAR", True),
            ("patient_id", "BIGINT", False), ("tat_hours", "FLOAT", False),
        ],
        "metrics": [
            ("Tests", "COUNT(analysis_id)"),
            ("Unique Patients", "COUNT(DISTINCT patient_id)"),
            ("Avg TAT (hrs)", "AVG(tat_hours)"),
        ],
    })

    # ---- Odoo: INVOICE LINES (revenue) ------------------------------------ #
    specs.append({
        "db": "odoo", "table_name": "fct_invoice_lines", "dttm": "invoiced_at",
        "sql": """
            SELECT
                aml.id AS line_id,
                am.id AS invoice_id,
                am.invoice_date AS invoiced_at,
                am.partner_id,
                COALESCE(pt.name->>'en_US', pt.name->>'en', 'Other') AS product,
                COALESCE(pc.name, 'Other') AS product_category,
                COALESCE(am.payment_state, 'unknown') AS payment_state,
                aml.price_subtotal AS amount
            FROM account_move am
            JOIN account_move_line aml ON am.id = aml.move_id
                AND (aml.display_type IS NULL OR aml.display_type = 'product')
            LEFT JOIN product_product pp ON aml.product_id = pp.id
            LEFT JOIN product_template pt ON pp.product_tmpl_id = pt.id
            LEFT JOIN product_category pc ON pt.categ_id = pc.id
            WHERE am.move_type = 'out_invoice' AND am.state = 'posted'
        """,
        "columns": [
            ("line_id", "BIGINT", False), ("invoice_id", "BIGINT", False),
            ("invoiced_at", "TIMESTAMP", False), ("partner_id", "BIGINT", False),
            ("product", "VARCHAR", True), ("product_category", "VARCHAR", True),
            ("payment_state", "VARCHAR", True), ("amount", "FLOAT", False),
        ],
        "metrics": [
            ("Revenue", "SUM(amount)"),
            ("Invoices", "COUNT(DISTINCT invoice_id)"),
            ("Customers", "COUNT(DISTINCT partner_id)"),
            ("Avg Line Value", "AVG(amount)"),
        ],
    })

    # ---- Odoo: OUTSTANDING AR (invoice header grain) ---------------------- #
    specs.append({
        "db": "odoo", "table_name": "fct_outstanding_ar", "dttm": "invoiced_at",
        "sql": """
            SELECT
                am.id AS invoice_id,
                am.invoice_date AS invoiced_at,
                am.partner_id,
                am.amount_total,
                am.amount_residual,
                COALESCE(am.payment_state, 'unknown') AS payment_state,
                (CURRENT_DATE - am.invoice_date) AS days_outstanding,
                CASE
                    WHEN CURRENT_DATE - am.invoice_date <= 30 THEN '0-30 days'
                    WHEN CURRENT_DATE - am.invoice_date <= 60 THEN '31-60 days'
                    WHEN CURRENT_DATE - am.invoice_date <= 90 THEN '61-90 days'
                    ELSE '90+ days' END AS aging_bucket
            FROM account_move am
            WHERE am.move_type = 'out_invoice' AND am.state = 'posted'
              AND am.payment_state <> 'paid'
        """,
        "columns": [
            ("invoice_id", "BIGINT", False), ("invoiced_at", "TIMESTAMP", False),
            ("partner_id", "BIGINT", False), ("amount_total", "FLOAT", False),
            ("amount_residual", "FLOAT", False), ("payment_state", "VARCHAR", True),
            ("days_outstanding", "INTEGER", False), ("aging_bucket", "VARCHAR", True),
        ],
        "metrics": [
            ("Outstanding", "SUM(amount_residual)"),
            ("Billed", "SUM(amount_total)"),
            ("Open Invoices", "COUNT(DISTINCT invoice_id)"),
        ],
    })

    # ---- Odoo: DRUG STOCK (snapshot, non-temporal) ------------------------ #
    specs.append({
        "db": "odoo", "table_name": "fct_drug_stock", "dttm": None,
        "sql": """
            SELECT
                pt.id AS product_id,
                COALESCE(pt.name->>'en_US', pt.name->>'en', 'Unknown') AS drug_name,
                COALESCE(sl.name, 'No Lot') AS lot_number,
                COALESCE(loc.complete_name, 'Unknown') AS location,
                SUM(sq.quantity) AS quantity
            FROM stock_quant sq
            JOIN product_product pp ON sq.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            JOIN stock_location loc ON sq.location_id = loc.id
            LEFT JOIN stock_lot sl ON sq.lot_id = sl.id
            WHERE pt.clinical_product_type = 'drug'
              AND loc.usage = 'internal'
              AND sq.quantity > 0
            GROUP BY pt.id, pt.name, sl.name, loc.complete_name
        """,
        "columns": [
            ("product_id", "BIGINT", False), ("drug_name", "VARCHAR", True),
            ("lot_number", "VARCHAR", True), ("location", "VARCHAR", True),
            ("quantity", "FLOAT", False),
        ],
        "metrics": [("Quantity on Hand", "SUM(quantity)")],
    })

    return specs


# --------------------------------------------------------------------------- #
# CHART helpers — every chart leaves time range/grain to the dashboard filters
# (time_range="No filter", time_grain default P1D overridden by the grain filter)
# --------------------------------------------------------------------------- #
def ts_chart(name, table, metrics, x_axis, groupby=None, viz="echarts_timeseries_line"):
    return (name, table, viz, {
        "x_axis": x_axis,
        "time_grain_sqla": "P1D",
        "metrics": metrics,
        "groupby": groupby or [],
        "row_limit": 10000,
        "time_range": "No filter",
        "x_axis_sort_asc": True,
        "show_legend": True,
        "rich_tooltip": True,
        "adhoc_filters": [],
    })


def cat_bar_chart(name, table, metric, dimension, row_limit=25):
    return (name, table, "echarts_timeseries_bar", {
        "x_axis": dimension,
        "metrics": [metric],
        "groupby": [],
        "row_limit": row_limit,
        "time_range": "No filter",
        "order_desc": True,
        "show_legend": False,
        "adhoc_filters": [],
    })


def pie_chart(name, table, metric, dimension):
    return (name, table, "pie", {
        "metric": metric,
        "groupby": [dimension],
        "row_limit": 50,
        "time_range": "No filter",
        "show_legend": True,
        "legendType": "scroll",
        "adhoc_filters": [],
    })


def big_number(name, table, metric, subheader=""):
    return (name, table, "big_number_total", {
        "metric": metric,
        "time_range": "No filter",
        "subheader": subheader,
        "adhoc_filters": [],
    })


def table_chart(name, table, columns, metrics=None, row_limit=100, order_by=None):
    params = {
        "query_mode": "aggregate" if metrics else "raw",
        "row_limit": row_limit,
        "time_range": "No filter",
        "adhoc_filters": [],
    }
    if metrics:
        params["groupby"] = columns
        params["metrics"] = metrics
    else:
        params["all_columns"] = columns
    if order_by:
        params["order_by_cols"] = order_by
    return (name, table, "table", params)


def build_chart_specs():
    M = lambda *names: list(names)  # saved-metric references by name
    charts = []

    # Executive KPIs
    charts += [
        big_number("KPI · Patients Registered", "fct_patients", "Patients"),
        big_number("KPI · Visits", "fct_visits", "Visits"),
        big_number("KPI · Lab Tests", "fct_lab_orders", "Tests"),
        big_number("KPI · Revenue", "fct_invoice_lines", "Revenue"),
        ts_chart("Patient Registrations Trend", "fct_patients", ["Patients"], "registered_at"),
        ts_chart("Visits Trend by Type", "fct_visits", ["Visits"], "started_at", groupby=["visit_type"]),
        ts_chart("Revenue Trend", "fct_invoice_lines", ["Revenue"], "invoiced_at"),
        ts_chart("Lab Volume Trend", "fct_lab_orders", ["Tests"], "ordered_at"),
    ]

    # Patient flow & demographics
    charts += [
        pie_chart("Gender Distribution", "fct_patients", "Patients", "gender"),
        cat_bar_chart("Age Distribution", "fct_patients", "Patients", "age_group"),
        cat_bar_chart("Top Municipalities", "fct_patients", "Patients", "municipality"),
        cat_bar_chart("Patients by District", "fct_patients", "Patients", "district"),
        cat_bar_chart("Visits by Hour of Day", "fct_visits", "Visits", "hour_of_day", row_limit=24),
        pie_chart("Visits by Status", "fct_visits", "Visits", "visit_status"),
    ]

    # Clinical operations
    charts += [
        cat_bar_chart("Encounter Types", "fct_encounters", "Encounters", "encounter_type"),
        cat_bar_chart("Provider Productivity", "fct_encounters", "Encounters", "provider_name"),
        cat_bar_chart("Encounters by Location", "fct_encounters", "Encounters", "location"),
        ts_chart("Avg Length of Stay", "fct_visits", ["Avg LOS (days)"], "started_at", groupby=["visit_type"]),
    ]

    # Morbidity / public health
    charts += [
        cat_bar_chart("Top Diagnoses", "fct_diagnoses", "Diagnoses", "diagnosis", row_limit=25),
        cat_bar_chart("Morbidity by Age Group", "fct_diagnoses", "Diagnoses", "age_group"),
        pie_chart("Morbidity by Gender", "fct_diagnoses", "Diagnoses", "gender"),
        ts_chart("Diagnosis Trend", "fct_diagnoses", ["Diagnoses"], "diagnosed_at"),
    ]

    # Laboratory
    charts += [
        ts_chart("Lab Test Volume", "fct_lab_orders", ["Tests"], "ordered_at", groupby=["test_section"]),
        cat_bar_chart("Top Tests", "fct_lab_orders", "Tests", "test_name", row_limit=25),
        ts_chart("Turnaround Time Trend", "fct_lab_orders", ["Avg TAT (hrs)"], "ordered_at", groupby=["test_section"]),
        pie_chart("Tests by Status", "fct_lab_orders", "Tests", "status"),
    ]

    # Financial
    charts += [
        ts_chart("Revenue by Category Trend", "fct_invoice_lines", ["Revenue"], "invoiced_at", groupby=["product_category"]),
        cat_bar_chart("Revenue by Category", "fct_invoice_lines", "Revenue", "product_category"),
        cat_bar_chart("Top Revenue Products", "fct_invoice_lines", "Revenue", "product", row_limit=25),
        cat_bar_chart("AR Aging", "fct_outstanding_ar", "Outstanding", "aging_bucket"),
        pie_chart("Invoices by Payment State", "fct_invoice_lines", "Invoices", "payment_state"),
    ]

    # Pharmacy
    charts += [
        cat_bar_chart("Top Prescribed Drugs", "fct_drug_orders", "Prescriptions", "drug_name", row_limit=25),
        ts_chart("Prescription Trend", "fct_drug_orders", ["Prescriptions"], "ordered_at"),
        table_chart("Stock on Hand", "fct_drug_stock",
                    ["drug_name", "lot_number", "location"], metrics=["Quantity on Hand"], row_limit=200),
    ]

    # Inpatient / bed management
    charts += [
        big_number("KPI · Currently Admitted", "fct_bed_assignments", "Admissions"),
        ts_chart("Admissions Trend", "fct_bed_assignments", ["Admissions"], "assigned_at", groupby=["ward"]),
        cat_bar_chart("Bed Days by Ward", "fct_bed_assignments", "Bed Days", "ward"),
        pie_chart("Beds by Type", "fct_bed_assignments", "Admissions", "bed_type"),
    ]

    return charts


# --------------------------------------------------------------------------- #
# DASHBOARD SPECS
# filters: list of (table_name, column, label). Every dashboard also gets a
# global Time Range + Time Grain filter automatically.
# --------------------------------------------------------------------------- #
def build_dashboard_specs():
    return [
        {
            "title": "NidanEHR · Executive Overview",
            "charts": ["KPI · Patients Registered", "KPI · Visits", "KPI · Lab Tests", "KPI · Revenue",
                       "Patient Registrations Trend", "Revenue Trend",
                       "Visits Trend by Type", "Lab Volume Trend"],
            "filters": [("fct_visits", "visit_type", "Visit Type"),
                        ("fct_visits", "location", "Location"),
                        ("fct_patients", "gender", "Gender")],
        },
        {
            "title": "NidanEHR · Patient Flow & Demographics",
            "charts": ["Gender Distribution", "Age Distribution", "Top Municipalities",
                       "Patients by District", "Visits by Hour of Day", "Visits by Status"],
            "filters": [("fct_patients", "gender", "Gender"),
                        ("fct_patients", "age_group", "Age Group"),
                        ("fct_patients", "district", "District"),
                        ("fct_patients", "province", "Province")],
        },
        {
            "title": "NidanEHR · Clinical Operations",
            "charts": ["Encounter Types", "Provider Productivity", "Encounters by Location",
                       "Avg Length of Stay"],
            "filters": [("fct_encounters", "encounter_type", "Encounter Type"),
                        ("fct_encounters", "location", "Location"),
                        ("fct_encounters", "provider_name", "Provider")],
        },
        {
            "title": "NidanEHR · Morbidity & Public Health",
            "charts": ["Top Diagnoses", "Morbidity by Age Group", "Morbidity by Gender",
                       "Diagnosis Trend"],
            "filters": [("fct_diagnoses", "diagnosis", "Diagnosis"),
                        ("fct_diagnoses", "age_group", "Age Group"),
                        ("fct_diagnoses", "gender", "Gender"),
                        ("fct_diagnoses", "dx_rank", "Diagnosis Rank")],
        },
        {
            "title": "NidanEHR · Laboratory Analytics",
            "charts": ["Lab Test Volume", "Top Tests", "Turnaround Time Trend", "Tests by Status"],
            "filters": [("fct_lab_orders", "test_section", "Test Section"),
                        ("fct_lab_orders", "test_name", "Test"),
                        ("fct_lab_orders", "status", "Status")],
        },
        {
            "title": "NidanEHR · Financial Performance",
            "charts": ["Revenue by Category Trend", "Revenue by Category", "Top Revenue Products",
                       "AR Aging", "Invoices by Payment State"],
            "filters": [("fct_invoice_lines", "product_category", "Service Category"),
                        ("fct_invoice_lines", "payment_state", "Payment State")],
        },
        {
            "title": "NidanEHR · Pharmacy Management",
            "charts": ["Top Prescribed Drugs", "Prescription Trend", "Stock on Hand"],
            "filters": [("fct_drug_orders", "drug_name", "Drug")],
        },
        {
            "title": "NidanEHR · Inpatient & Bed Management",
            "charts": ["KPI · Currently Admitted", "Admissions Trend", "Bed Days by Ward", "Beds by Type"],
            "filters": [("fct_bed_assignments", "ward", "Ward"),
                        ("fct_bed_assignments", "bed_type", "Bed Type"),
                        ("fct_bed_assignments", "bed_status", "Bed Status")],
        },
    ]


# --------------------------------------------------------------------------- #
# Layout + native-filter JSON generators
# --------------------------------------------------------------------------- #
def _hash_id(prefix, *parts):
    h = hashlib.md5("::".join(str(p) for p in parts).encode()).hexdigest()[:10]
    return f"{prefix}-{h}"


def build_position_json(title, slices):
    """Grid layout: KPI/big-number charts compact, others 2-per-row."""
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
    }
    rows = []
    kpis = [s for s in slices if s.viz_type == "big_number_total"]
    others = [s for s in slices if s.viz_type != "big_number_total"]

    def add_row(members, width, height):
        if not members:
            return
        row_id = _hash_id("ROW", title, len(rows))
        children = []
        for s in members:
            cid = _hash_id("CHART", s.id)
            position[cid] = {
                "type": "CHART", "id": cid, "children": [],
                "meta": {"chartId": s.id, "sliceName": s.slice_name,
                         "width": width, "height": height},
                "parents": ["ROOT_ID", "GRID_ID", row_id],
            }
            children.append(cid)
        position[row_id] = {
            "type": "ROW", "id": row_id, "children": children,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
            "parents": ["ROOT_ID", "GRID_ID"],
        }
        rows.append(row_id)

    # KPI row: up to 4 across
    for i in range(0, len(kpis), 4):
        add_row(kpis[i:i + 4], width=3, height=40)
    # Other charts: 2 across
    for i in range(0, len(others), 2):
        add_row(others[i:i + 2], width=6, height=52)

    position["GRID_ID"]["children"] = rows
    return position


def build_native_filters(title, filter_specs, table_to_dataset, primary_dataset_id):
    """Time Range + Time Grain + one select per (table, column, label)."""
    filters = []

    # Time Range (presets + custom range). Default to a generous window.
    filters.append({
        "id": _hash_id("NATIVE_FILTER", title, "time_range"),
        "name": "Time Range",
        "filterType": "filter_time",
        "type": "NATIVE_FILTER",
        "targets": [{}],
        "controlValues": {},
        "defaultDataMask": {
            "filterState": {"value": "Last 90 days"},
            "extraFormData": {"time_range": "Last 90 days"},
        },
        "cascadeParentIds": [],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "description": "",
    })

    # Time Grain: Day / Week / Month / Quarter / Year
    filters.append({
        "id": _hash_id("NATIVE_FILTER", title, "time_grain"),
        "name": "Time Grain",
        "filterType": "filter_timegrain",
        "type": "NATIVE_FILTER",
        "targets": [{"datasetId": primary_dataset_id}],
        "controlValues": {},
        "defaultDataMask": {
            "filterState": {"value": ["P1D"]},
            "extraFormData": {"time_grain_sqla": "P1D"},
        },
        "cascadeParentIds": [],
        "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
        "description": "Daily / Weekly / Monthly / Quarterly / Yearly",
    })

    # Value (data-element) filters
    for table_name, column, label in filter_specs:
        ds_id = table_to_dataset.get(table_name)
        if not ds_id:
            continue
        filters.append({
            "id": _hash_id("NATIVE_FILTER", title, table_name, column),
            "name": label,
            "filterType": "filter_select",
            "type": "NATIVE_FILTER",
            "targets": [{"datasetId": ds_id, "column": {"name": column}}],
            "controlValues": {
                "multiSelect": True, "enableEmptyFilter": False,
                "searchAllOptions": False, "inverseSelection": False,
                "defaultToFirstItem": False,
            },
            "defaultDataMask": {"filterState": {}, "extraFormData": {}},
            "cascadeParentIds": [],
            "scope": {"rootPath": ["ROOT_ID"], "excluded": []},
            "description": "",
        })
    return filters


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main():
    from superset.app import create_app

    app = create_app()

    openmrs_db_type = os.environ.get("OPENMRS_DB_TYPE", "mysql")
    DB_CONFIGS = {
        "openmrs": {"name": "OpenMRS", "uri": get_db_uri("OPENMRS_DB", "openmrs-db", openmrs_db_type)},
        "openelis": {"name": "OpenELIS", "uri": get_db_uri("OPENELIS_DB", "openelis-db")},
        "odoo": {"name": "Odoo", "uri": get_db_uri("ODOO_DB", "odoo-db")},
    }

    with app.app_context():
        from superset.extensions import db
        from superset.models.core import Database
        from superset.connectors.sqla.models import SqlaTable, TableColumn, SqlMetric
        from superset.models.slice import Slice
        from superset.models.dashboard import Dashboard

        print("=" * 70)
        print("NidanEHR — Configurable Hospital Analytics Initialization")
        print("=" * 70)

        # --- Step 1: database connections --------------------------------- #
        print("\n[1/4] Database connections")
        databases = {}
        for key, config in DB_CONFIGS.items():
            existing = db.session.query(Database).filter_by(database_name=config["name"]).first()
            if existing:
                if existing.allow_run_async:
                    existing.allow_run_async = False
                    db.session.commit()
                databases[key] = existing
                print(f"  ✓ {config['name']} (existing)")
            else:
                database = Database(
                    database_name=config["name"], sqlalchemy_uri=config["uri"],
                    expose_in_sqllab=True, allow_run_async=False,
                    allow_ctas=False, allow_cvas=False, allow_dml=False,
                )
                db.session.add(database)
                db.session.commit()
                databases[key] = database
                print(f"  ✓ {config['name']} (created)")

        # --- Step 2: datasets --------------------------------------------- #
        print("\n[2/4] Datasets (wide facts)")
        datasets = {}            # table_name -> SqlaTable
        table_to_dataset = {}    # table_name -> dataset id

        def ensure_columns(dataset, columns):
            existing = {c.column_name: c for c in dataset.columns}
            for name, ctype, is_dim in columns:
                is_dttm = ctype in ("DATE", "TIMESTAMP", "DATETIME")
                col = existing.get(name)
                if not col:
                    col = TableColumn(column_name=name, table=dataset)
                    db.session.add(col)
                col.type = ctype
                col.is_dttm = is_dttm
                col.groupby = bool(is_dim)
                col.filterable = bool(is_dim) or is_dttm

        def ensure_metrics(dataset, metrics):
            existing = {m.metric_name: m for m in dataset.metrics}
            for name, expr in metrics:
                m = existing.get(name)
                if not m:
                    m = SqlMetric(metric_name=name, table=dataset)
                    db.session.add(m)
                m.expression = expr

        def upsert_dataset(spec):
            database = databases.get(spec["db"])
            if not database:
                return
            tname, sql = spec["table_name"], spec["sql"].strip()
            ds = db.session.query(SqlaTable).filter_by(table_name=tname, database_id=database.id).first()
            created = False
            if not ds:
                ds = SqlaTable(table_name=tname, sql=sql, database_id=database.id,
                               database=database, is_sqllab_view=False)
                db.session.add(ds)
                created = True
            else:
                ds.sql = sql
            if spec.get("dttm"):
                ds.main_dttm_col = spec["dttm"]
            db.session.commit()
            ensure_columns(ds, spec["columns"])
            ensure_metrics(ds, spec["metrics"])
            db.session.commit()
            datasets[tname] = ds
            table_to_dataset[tname] = ds.id
            print(f"  ✓ {tname} ({'created' if created else 'updated'})")

        for spec in build_dataset_specs():
            try:
                upsert_dataset(spec)
            except Exception as e:
                db.session.rollback()
                print(f"  ✗ {spec['table_name']}: {e}")

        # --- Step 3: charts ----------------------------------------------- #
        print("\n[3/4] Charts")
        charts = {}

        def upsert_chart(name, table_name, viz_type, params):
            ds = datasets.get(table_name)
            if not ds:
                return
            params = dict(params)
            params.setdefault("datasource", f"{ds.id}__table")
            params["viz_type"] = viz_type
            existing = db.session.query(Slice).filter_by(
                slice_name=name, datasource_id=ds.id, datasource_type="table").first()
            if existing:
                existing.viz_type = viz_type
                existing.params = json.dumps(params)
                db.session.commit()
                charts[name] = existing
            else:
                s = Slice(slice_name=name, datasource_id=ds.id, datasource_type="table",
                          viz_type=viz_type, params=json.dumps(params))
                db.session.add(s)
                db.session.commit()
                charts[name] = s

        for name, table_name, viz_type, params in build_chart_specs():
            try:
                upsert_chart(name, table_name, viz_type, params)
            except Exception as e:
                db.session.rollback()
                print(f"  ✗ chart '{name}': {e}")
        print(f"  ✓ {len(charts)} charts ready")

        # --- Step 4: dashboards (with universal filters) ------------------ #
        print("\n[4/4] Dashboards + native filters")

        def upsert_dashboard(spec):
            title = spec["title"]
            slices = [charts[n] for n in spec["charts"] if n in charts]
            if not slices:
                print(f"  ⚠ {title}: no charts available, skipping")
                return
            primary_ds_id = slices[0].datasource_id
            position = build_position_json(title, slices)
            native_filters = build_native_filters(
                title, spec.get("filters", []), table_to_dataset, primary_ds_id)
            json_metadata = json.dumps({
                "native_filter_configuration": native_filters,
                "cross_filters_enabled": True,
                "color_scheme": "supersetColors",
                "refresh_frequency": 0,
                "expanded_slices": {},
                "label_colors": {},
                "shared_label_colors": {},
                "timed_refresh_immune_slices": [],
                "default_filters": "{}",
                "filter_scopes": {},
                "chart_configuration": {},
            })
            dash = db.session.query(Dashboard).filter_by(dashboard_title=title).first()
            if not dash:
                dash = Dashboard(dashboard_title=title,
                                 slug=title.lower().replace(" ", "-").replace("·", "").replace("&", "and").replace("--", "-")[:50],
                                 published=True)
                db.session.add(dash)
            dash.slices = slices
            dash.position_json = json.dumps(position)
            dash.json_metadata = json_metadata
            db.session.commit()
            print(f"  ✓ {title} ({len(slices)} charts, {len(native_filters)} filters)")

        for spec in build_dashboard_specs():
            try:
                upsert_dashboard(spec)
            except Exception as e:
                db.session.rollback()
                print(f"  ✗ dashboard '{spec['title']}': {e}")

        print("\n" + "=" * 70)
        print("✅ Done.  http://localhost/superset/dashboard/list/")
        print("=" * 70)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
