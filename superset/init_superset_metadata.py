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

# Per-chart layout width (in a 12-col grid), populated by the chart helpers and
# consumed by build_position_json for greedy row packing (competitor-style mix
# of full-width / 3-col / 2-col rows instead of a fixed 2-column layout).
CHART_WIDTHS = {}


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
                {age_group_mysql('pe.birthdate', 'ed.date_created')} AS age_group,
                COALESCE(pa.city_village, 'Unknown') AS municipality,
                COALESCE(pa.county_district, 'Unknown') AS district
            FROM encounter_diagnosis ed
            LEFT JOIN concept_name cn ON ed.diagnosis_coded = cn.concept_id
                AND cn.locale = 'en' AND cn.concept_name_type = 'FULLY_SPECIFIED'
                AND cn.voided = 0
            JOIN person pe ON ed.patient_id = pe.person_id
            LEFT JOIN (
                SELECT person_id, city_village, county_district,
                       ROW_NUMBER() OVER (PARTITION BY person_id
                           ORDER BY preferred DESC, person_address_id DESC) AS rn
                FROM person_address WHERE voided = 0
            ) pa ON pa.person_id = ed.patient_id AND pa.rn = 1
            WHERE ed.voided = 0
        """,
        "columns": [
            ("diagnosis_id", "BIGINT", False), ("patient_id", "BIGINT", False),
            ("diagnosed_at", "TIMESTAMP", False), ("diagnosis", "VARCHAR", True),
            ("dx_rank", "VARCHAR", True), ("certainty", "VARCHAR", True),
            ("gender", "VARCHAR", True), ("age_group", "VARCHAR", True),
            ("municipality", "VARCHAR", True), ("district", "VARCHAR", True),
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

    # ---- Odoo: POS PAYMENTS (tender grain) -------------------------------- #
    # One row per pos.payment tender line. Drives "payment method vs amount"
    # and "discount provided": the nidan_pos_discount module models program
    # discounts as POS payment methods flagged is_nidan_program_discount, so
    # discounts are just tender lines we split out via payment_kind.
    # pos.payment.method.name is translate=True -> jsonb, hence the ->> extract.
    specs.append({
        "db": "odoo", "table_name": "fct_pos_payments", "dttm": "paid_at",
        "sql": """
            SELECT
                pp.id AS payment_id,
                pp.payment_date AS paid_at,
                pp.pos_order_id,
                po.partner_id,
                COALESCE(NULLIF(TRIM(ppm.name->>'en_US'), ''),
                         NULLIF(TRIM(ppm.name->>'en'), ''), 'Unknown') AS payment_method,
                CASE WHEN COALESCE(ppm.is_nidan_program_discount, false)
                     THEN 'Discount' ELSE 'Payment' END AS payment_kind,
                CASE
                    WHEN ppm.discount_funding_type = 'hospital' THEN 'Hospital-funded'
                    WHEN ppm.discount_funding_type = 'claimable' THEN 'Externally funded'
                    ELSE 'n/a' END AS discount_funding,
                pp.amount AS amount
            FROM pos_payment pp
            JOIN pos_order po ON pp.pos_order_id = po.id
            LEFT JOIN pos_payment_method ppm ON pp.payment_method_id = ppm.id
            WHERE po.state NOT IN ('draft', 'cancel')
        """,
        "columns": [
            ("payment_id", "BIGINT", False), ("paid_at", "TIMESTAMP", False),
            ("pos_order_id", "BIGINT", False), ("partner_id", "BIGINT", False),
            ("payment_method", "VARCHAR", True), ("payment_kind", "VARCHAR", True),
            ("discount_funding", "VARCHAR", True), ("amount", "FLOAT", False),
        ],
        "metrics": [
            ("Amount", "SUM(amount)"),
            ("Payments", "COUNT(payment_id)"),
            ("Orders", "COUNT(DISTINCT pos_order_id)"),
        ],
    })

    # ---- Odoo: INSURANCE CLAIMS (claim header grain) --------------------- #
    # From nidan_insurance_management.insurance.claim. insurance_type is stored
    # as the insurance.type *code* (char), joined to insurance_type for the
    # human-readable scheme name (case-insensitive: claims may carry lowercase
    # 'hib' while the type code is 'HIB'). claimed = claim_amount (billed to
    # insurer), settled = paid_amount (actually paid by the insurer).
    specs.append({
        "db": "odoo", "table_name": "fct_insurance_claims", "dttm": "claimed_at",
        "sql": """
            SELECT
                ic.id AS claim_id,
                ic.claim_date AS claimed_at,
                ic.patient_id,
                COALESCE(NULLIF(TRIM(it.name), ''), ic.insurance_type, 'Unknown') AS insurance_type,
                CASE ic.state
                    WHEN 'draft' THEN 'Draft'
                    WHEN 'submitted' THEN 'Submitted'
                    WHEN 'under_review' THEN 'Under Review'
                    WHEN 'approved' THEN 'Approved'
                    WHEN 'partially_approved' THEN 'Partially Approved'
                    WHEN 'rejected' THEN 'Rejected'
                    WHEN 'paid' THEN 'Paid'
                    WHEN 'cancelled' THEN 'Cancelled'
                    ELSE ic.state END AS claim_status,
                ic.claim_amount AS claimed_amount,
                ic.approved_amount AS approved_amount,
                ic.paid_amount AS settled_amount,
                ic.gross_claim_amount AS gross_amount
            FROM insurance_claim ic
            LEFT JOIN (
                SELECT DISTINCT ON (LOWER(code)) LOWER(code) AS lcode, name
                FROM insurance_type
                ORDER BY LOWER(code), active DESC, id DESC
            ) it ON it.lcode = LOWER(ic.insurance_type)
        """,
        "columns": [
            ("claim_id", "BIGINT", False), ("claimed_at", "TIMESTAMP", False),
            ("patient_id", "BIGINT", False), ("insurance_type", "VARCHAR", True),
            ("claim_status", "VARCHAR", True), ("claimed_amount", "FLOAT", False),
            ("approved_amount", "FLOAT", False), ("settled_amount", "FLOAT", False),
            ("gross_amount", "FLOAT", False),
        ],
        "metrics": [
            ("Claims", "COUNT(claim_id)"),
            ("Claimed Amount", "SUM(claimed_amount)"),
            ("Approved Amount", "SUM(approved_amount)"),
            ("Settled Amount", "SUM(settled_amount)"),
        ],
    })

    # ---- Odoo: DRUG STOCK BY LOT (expiry + value, snapshot) -------------- #
    # product_expiry module is installed -> stock_lot.expiration_date is real.
    # value-at-risk uses list_price (sale price) as the valuation proxy.
    specs.append({
        "db": "odoo", "table_name": "fct_drug_stock", "dttm": None,
        "sql": """
            SELECT
                pt.id AS product_id,
                COALESCE(pt.name->>'en_US', pt.name->>'en', 'Unknown') AS drug_name,
                COALESCE(pc.name, 'Other') AS drug_category,
                COALESCE(sl.name, 'No Lot') AS lot_number,
                COALESCE(loc.complete_name, 'Unknown') AS location,
                sl.expiration_date::date AS expiration_date,
                SUM(sq.quantity) AS quantity,
                SUM(sq.quantity * COALESCE(pt.list_price, 0)) AS stock_value,
                (sl.expiration_date::date - CURRENT_DATE) AS days_until_expiry,
                CASE
                    WHEN sl.expiration_date IS NULL THEN 'No expiry date'
                    WHEN sl.expiration_date::date < CURRENT_DATE THEN 'Expired'
                    WHEN sl.expiration_date::date <= CURRENT_DATE + 30 THEN '<= 30 days'
                    WHEN sl.expiration_date::date <= CURRENT_DATE + 60 THEN '31-60 days'
                    WHEN sl.expiration_date::date <= CURRENT_DATE + 90 THEN '61-90 days'
                    WHEN sl.expiration_date::date <= CURRENT_DATE + 180 THEN '91-180 days'
                    ELSE '> 180 days' END AS expiry_bucket
            FROM stock_quant sq
            JOIN product_product pp ON sq.product_id = pp.id
            JOIN product_template pt ON pp.product_tmpl_id = pt.id
            JOIN stock_location loc ON sq.location_id = loc.id
            LEFT JOIN product_category pc ON pt.categ_id = pc.id
            LEFT JOIN stock_lot sl ON sq.lot_id = sl.id
            WHERE pt.clinical_product_type = 'drug'
              AND loc.usage = 'internal'
              AND sq.quantity > 0
            GROUP BY pt.id, pt.name, pc.name, sl.name, loc.complete_name, sl.expiration_date
        """,
        "columns": [
            ("product_id", "BIGINT", False), ("drug_name", "VARCHAR", True),
            ("drug_category", "VARCHAR", True), ("lot_number", "VARCHAR", True),
            ("location", "VARCHAR", True), ("expiration_date", "DATE", False),
            ("quantity", "FLOAT", False), ("stock_value", "FLOAT", False),
            ("days_until_expiry", "INTEGER", False), ("expiry_bucket", "VARCHAR", True),
        ],
        "metrics": [
            ("Quantity on Hand", "SUM(quantity)"),
            ("Stock Value", "SUM(stock_value)"),
            ("Lots", "COUNT(*)"),
            ("SKUs", "COUNT(DISTINCT product_id)"),
        ],
    })

    # ---- Odoo: DRUG ON-HAND BY PRODUCT (levels vs reorder) --------------- #
    # Starts from the product list so out-of-stock items appear; left-joins
    # on-hand and reorder rules (stock.warehouse.orderpoint).
    specs.append({
        "db": "odoo", "table_name": "fct_drug_onhand", "dttm": None,
        "sql": """
            SELECT
                pt.id AS product_id,
                COALESCE(pt.name->>'en_US', pt.name->>'en', 'Unknown') AS drug_name,
                COALESCE(pc.name, 'Other') AS drug_category,
                COALESCE(oh.qty, 0) AS on_hand,
                COALESCE(pt.list_price, 0) AS unit_price,
                COALESCE(oh.qty, 0) * COALESCE(pt.list_price, 0) AS stock_value,
                op.reorder_level,
                CASE
                    WHEN COALESCE(oh.qty, 0) <= 0 THEN 'Out of stock'
                    WHEN op.reorder_level IS NOT NULL AND COALESCE(oh.qty, 0) <= op.reorder_level THEN 'Below reorder'
                    WHEN COALESCE(oh.qty, 0) < 20 THEN 'Low (<20)'
                    ELSE 'OK' END AS stock_status
            FROM product_template pt
            LEFT JOIN product_category pc ON pt.categ_id = pc.id
            LEFT JOIN (
                SELECT pp.product_tmpl_id AS tmpl_id, SUM(sq.quantity) AS qty
                FROM stock_quant sq
                JOIN stock_location l ON sq.location_id = l.id AND l.usage = 'internal'
                JOIN product_product pp ON sq.product_id = pp.id
                GROUP BY pp.product_tmpl_id
            ) oh ON oh.tmpl_id = pt.id
            LEFT JOIN (
                SELECT pp.product_tmpl_id AS tmpl_id, MAX(op.product_min_qty) AS reorder_level
                FROM stock_warehouse_orderpoint op
                JOIN product_product pp ON op.product_id = pp.id
                GROUP BY pp.product_tmpl_id
            ) op ON op.tmpl_id = pt.id
            WHERE pt.clinical_product_type = 'drug' AND pt.active = true
        """,
        "columns": [
            ("product_id", "BIGINT", False), ("drug_name", "VARCHAR", True),
            ("drug_category", "VARCHAR", True), ("on_hand", "FLOAT", False),
            ("unit_price", "FLOAT", False), ("stock_value", "FLOAT", False),
            ("reorder_level", "FLOAT", False), ("stock_status", "VARCHAR", True),
        ],
        "metrics": [
            ("Stock Value", "SUM(stock_value)"),
            ("On Hand", "SUM(on_hand)"),
            ("SKUs", "COUNT(DISTINCT product_id)"),
            ("Out-of-stock SKUs", "SUM(CASE WHEN stock_status = 'Out of stock' THEN 1 ELSE 0 END)"),
            ("Reorder-alert SKUs", "SUM(CASE WHEN stock_status IN ('Out of stock', 'Below reorder', 'Low (<20)') THEN 1 ELSE 0 END)"),
        ],
    })

    # ---- OpenMRS: INPATIENT OUTCOMES (HMIS core) -------------------------- #
    # Discharge outcome captured as an obs value_coded. We identify outcome
    # observations by their distinctive value set (matches dhis2 inpatient.sql).
    # If your concept names differ, extend the IN (...) list below. Neonatal
    # bands (<=28d) included so this also powers neonatal-mortality reports.
    OUTCOME_VALUES = ("'Recovered','Cured','Recovered/Cured','Death','Died',"
                      "'Expired','DOR','DAMA','LAMA','LAMA/DAMA','Referred out',"
                      "'Referred on request','Stable','Absconded'")
    specs.append({
        "db": "openmrs", "table_name": "fct_inpatient_outcomes", "dttm": "outcome_at",
        "sql": f"""
            SELECT
                o.obs_id, o.person_id AS patient_id,
                o.obs_datetime AS outcome_at,
                CASE
                    WHEN cn.name IN ('Referred out', 'Referred on request') THEN 'Referred Out'
                    WHEN cn.name IN ('DOR', 'DAMA', 'LAMA', 'LAMA/DAMA') THEN 'DOPR/LAMA'
                    WHEN cn.name IN ('Recovered', 'Cured', 'Recovered/Cured') THEN 'Recovered/Cured'
                    WHEN cn.name IN ('Death', 'Died', 'Expired') THEN 'Death'
                    ELSE cn.name END AS outcome,
                COALESCE(pe.gender, 'U') AS gender,
                CASE
                    WHEN TIMESTAMPDIFF(DAY, pe.birthdate, o.obs_datetime) BETWEEN 0 AND 7 THEN '0-7 days'
                    WHEN TIMESTAMPDIFF(DAY, pe.birthdate, o.obs_datetime) BETWEEN 8 AND 28 THEN '8-28 days'
                    WHEN TIMESTAMPDIFF(DAY, pe.birthdate, o.obs_datetime) BETWEEN 29 AND 365 THEN '29 days - 1 yr'
                    WHEN TIMESTAMPDIFF(YEAR, pe.birthdate, o.obs_datetime) BETWEEN 1 AND 4 THEN '01-04 yrs'
                    WHEN TIMESTAMPDIFF(YEAR, pe.birthdate, o.obs_datetime) BETWEEN 5 AND 14 THEN '05-14 yrs'
                    WHEN TIMESTAMPDIFF(YEAR, pe.birthdate, o.obs_datetime) BETWEEN 15 AND 19 THEN '15-19 yrs'
                    WHEN TIMESTAMPDIFF(YEAR, pe.birthdate, o.obs_datetime) BETWEEN 20 AND 29 THEN '20-29 yrs'
                    WHEN TIMESTAMPDIFF(YEAR, pe.birthdate, o.obs_datetime) BETWEEN 30 AND 39 THEN '30-39 yrs'
                    WHEN TIMESTAMPDIFF(YEAR, pe.birthdate, o.obs_datetime) BETWEEN 40 AND 49 THEN '40-49 yrs'
                    WHEN TIMESTAMPDIFF(YEAR, pe.birthdate, o.obs_datetime) BETWEEN 50 AND 59 THEN '50-59 yrs'
                    WHEN TIMESTAMPDIFF(YEAR, pe.birthdate, o.obs_datetime) BETWEEN 60 AND 69 THEN '60-69 yrs'
                    ELSE '70+ yrs' END AS age_group,
                CASE WHEN TIMESTAMPDIFF(DAY, pe.birthdate, o.obs_datetime) <= 28 THEN 'Neonate' ELSE 'Non-neonate' END AS neonatal
            FROM obs o
            JOIN concept_name cn ON o.value_coded = cn.concept_id
                AND cn.locale = 'en' AND cn.concept_name_type = 'FULLY_SPECIFIED' AND cn.voided = 0
            JOIN person pe ON o.person_id = pe.person_id
            WHERE o.voided = 0 AND cn.name IN ({OUTCOME_VALUES})
        """,
        "columns": [
            ("obs_id", "BIGINT", False), ("patient_id", "BIGINT", False),
            ("outcome_at", "TIMESTAMP", False), ("outcome", "VARCHAR", True),
            ("gender", "VARCHAR", True), ("age_group", "VARCHAR", True),
            ("neonatal", "VARCHAR", True),
        ],
        "metrics": [
            ("Outcomes", "COUNT(*)"),
            ("Patients", "COUNT(DISTINCT patient_id)"),
            ("Deaths", "SUM(CASE WHEN outcome = 'Death' THEN 1 ELSE 0 END)"),
            ("Mortality %", "SUM(CASE WHEN outcome = 'Death' THEN 1 ELSE 0 END) * 100.0 / NULLIF(COUNT(*), 0)"),
        ],
    })

    # ---- OpenMRS: DATA QUALITY — registration completeness --------------- #
    specs.append({
        "db": "openmrs", "table_name": "dq_patient_quality", "dttm": "registered_at",
        "sql": """
            SELECT
                p.patient_id,
                p.date_created AS registered_at,
                CASE WHEN pe.gender IN ('M', 'F') THEN 1 ELSE 0 END AS has_gender,
                CASE WHEN pe.birthdate IS NOT NULL THEN 1 ELSE 0 END AS has_birthdate,
                CASE WHEN pa.city_village IS NOT NULL AND pa.city_village <> '' THEN 1 ELSE 0 END AS has_address
            FROM patient p
            JOIN person pe ON p.patient_id = pe.person_id
            LEFT JOIN (
                SELECT person_id, city_village,
                       ROW_NUMBER() OVER (PARTITION BY person_id
                           ORDER BY preferred DESC, person_address_id DESC) AS rn
                FROM person_address WHERE voided = 0
            ) pa ON pa.person_id = pe.person_id AND pa.rn = 1
            WHERE p.voided = 0
        """,
        "columns": [
            ("patient_id", "BIGINT", False), ("registered_at", "TIMESTAMP", False),
            ("has_gender", "INTEGER", False), ("has_birthdate", "INTEGER", False),
            ("has_address", "INTEGER", False),
        ],
        "metrics": [
            ("Patients", "COUNT(*)"),
            ("% Gender Complete", "AVG(has_gender) * 100"),
            ("% Birthdate Complete", "AVG(has_birthdate) * 100"),
            ("% Address Complete", "AVG(has_address) * 100"),
        ],
    })

    # ---- OpenMRS: DATA QUALITY — diagnosis coding ------------------------ #
    specs.append({
        "db": "openmrs", "table_name": "dq_diagnosis_coding", "dttm": "diagnosed_at",
        "sql": """
            SELECT
                ed.diagnosis_id,
                ed.date_created AS diagnosed_at,
                CASE WHEN ed.diagnosis_coded IS NOT NULL THEN 1 ELSE 0 END AS is_coded
            FROM encounter_diagnosis ed
            WHERE ed.voided = 0
        """,
        "columns": [
            ("diagnosis_id", "BIGINT", False), ("diagnosed_at", "TIMESTAMP", False),
            ("is_coded", "INTEGER", False),
        ],
        "metrics": [
            ("Diagnoses", "COUNT(*)"),
            ("% Coded", "AVG(is_coded) * 100"),
        ],
    })

    # ---- OpenMRS: DATA QUALITY — voided rates (snapshot) ----------------- #
    specs.append({
        "db": "openmrs", "table_name": "dq_voided_rates", "dttm": None,
        "sql": """
            SELECT 'Patients' AS entity, SUM(voided) AS voided_count, COUNT(*) AS total FROM patient
            UNION ALL SELECT 'Visits', SUM(voided), COUNT(*) FROM visit
            UNION ALL SELECT 'Encounters', SUM(voided), COUNT(*) FROM encounter
            UNION ALL SELECT 'Observations', SUM(voided), COUNT(*) FROM obs
            UNION ALL SELECT 'Orders', SUM(voided), COUNT(*) FROM orders
        """,
        "columns": [
            ("entity", "VARCHAR", True), ("voided_count", "BIGINT", False),
            ("total", "BIGINT", False),
        ],
        "metrics": [
            ("Voided", "SUM(voided_count)"),
            ("Voided %", "SUM(voided_count) * 100.0 / NULLIF(SUM(total), 0)"),
        ],
    })

    return specs


# --------------------------------------------------------------------------- #
# CHART helpers — every chart leaves time range/grain to the dashboard filters
# (time_range="No filter", time_grain default P1D overridden by the grain filter)
# --------------------------------------------------------------------------- #
def ts_chart(name, table, metrics, x_axis, groupby=None, viz="echarts_timeseries_line", width=6):
    CHART_WIDTHS[name] = width
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


def cat_bar_chart(name, table, metric, dimension, row_limit=25, filters=None, width=4):
    CHART_WIDTHS[name] = width
    return (name, table, "echarts_timeseries_bar", {
        "x_axis": dimension,
        "metrics": [metric],
        "groupby": [],
        "row_limit": row_limit,
        "time_range": "No filter",
        "order_desc": True,
        "show_legend": False,
        "adhoc_filters": filters or [],
    })


def pie_chart(name, table, metric, dimension, filters=None, width=4):
    CHART_WIDTHS[name] = width
    return (name, table, "pie", {
        "metric": metric,
        "groupby": [dimension],
        "row_limit": 50,
        "time_range": "No filter",
        "show_legend": True,
        "legendType": "scroll",
        "adhoc_filters": filters or [],
    })


def adhoc(col, op, val):
    """Build a SIMPLE adhoc filter dict for baked-in chart cuts."""
    return {"clause": "WHERE", "subject": col, "operator": op,
            "comparator": val, "expressionType": "SIMPLE"}


def big_number(name, table, metric, subheader="", filters=None, width=3):
    CHART_WIDTHS[name] = width
    return (name, table, "big_number_total", {
        "metric": metric,
        "time_range": "No filter",
        "subheader": subheader,
        "adhoc_filters": filters or [],
    })


def heatmap_chart(name, table, metric, x_col, y_col, filters=None, width=6):
    CHART_WIDTHS[name] = width
    # Superset 4.x: legacy "heatmap" was removed; the registered viz is "heatmap_v2"
    # (x_axis / groupby[=Y] / metric). groupby is single-select (multi=false).
    return (name, table, "heatmap_v2", {
        "x_axis": x_col,
        "groupby": y_col,
        "metric": metric,
        "row_limit": 10000,
        "time_range": "No filter",
        "normalize_across": "heatmap",
        "linear_color_scheme": "blue_white_yellow",
        "legend_type": "continuous",
        "sort_x_axis": "alpha_asc",
        "sort_y_axis": "alpha_asc",
        "xscale_interval": -1,
        "yscale_interval": -1,
        "left_margin": "auto",
        "bottom_margin": "auto",
        "value_bounds": [None, None],
        "y_axis_format": "SMART_NUMBER",
        "x_axis_time_format": "smart_date",
        "show_legend": True,
        "show_percentage": False,
        "show_values": False,
        "adhoc_filters": filters or [],
    })


def stacked_bar(name, table, metric, x_col, series_col, filters=None, row_limit=10000, width=6):
    CHART_WIDTHS[name] = width
    return (name, table, "echarts_timeseries_bar", {
        "x_axis": x_col,
        "metrics": [metric],
        "groupby": [series_col],
        "stack": "Stack",
        "row_limit": row_limit,
        "time_range": "No filter",
        "show_legend": True,
        "adhoc_filters": filters or [],
    })


def multi_metric_bar(name, table, metrics, dimension, filters=None, row_limit=50,
                     stack=False, width=6):
    """Categorical bar with several metrics side-by-side (grouped) or stacked.
    e.g. claimed vs settled amount per insurance type."""
    CHART_WIDTHS[name] = width
    params = {
        "x_axis": dimension,
        "metrics": metrics,
        "groupby": [],
        "row_limit": row_limit,
        "time_range": "No filter",
        "order_desc": True,
        "show_legend": True,
        "adhoc_filters": filters or [],
    }
    if stack:
        params["stack"] = "Stack"
    return (name, table, "echarts_timeseries_bar", params)


def table_chart(name, table, columns, metrics=None, row_limit=100, order_by=None,
                width=6, filters=None, conditional_formatting=None):
    """order_by: list of (column, ascending_bool). conditional_formatting: list of
    {column, operator, targetValue, colorScheme} for cell highlighting."""
    CHART_WIDTHS[name] = width
    params = {
        "query_mode": "aggregate" if metrics else "raw",
        "row_limit": row_limit,
        "time_range": "No filter",
        "adhoc_filters": filters or [],
        "server_pagination": False,
    }
    if metrics:
        params["groupby"] = columns
        params["metrics"] = metrics
    else:
        params["all_columns"] = columns
    if order_by:
        params["order_by_cols"] = [json.dumps([c, asc]) for c, asc in order_by]
    if conditional_formatting:
        params["conditional_formatting"] = conditional_formatting
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
        # a. Payment method vs amount (POS tenders, excluding program discounts)
        cat_bar_chart("Payment Method vs Amount", "fct_pos_payments", "Amount", "payment_method",
                      filters=[adhoc("payment_kind", "==", "Payment")], width=6),
        # b. Insurance type vs claimed vs settled amount
        multi_metric_bar("Insurance · Claimed vs Settled", "fct_insurance_claims",
                         ["Claimed Amount", "Settled Amount"], "insurance_type", width=6),
        # c. Insurance type vs number of claims, grouped (stacked) by status
        stacked_bar("Insurance Claims by Status", "fct_insurance_claims", "Claims",
                    "insurance_type", "claim_status", width=6),
        # d. Discount program vs discount amount provided
        cat_bar_chart("Discount vs Discount Provided", "fct_pos_payments", "Amount", "payment_method",
                      filters=[adhoc("payment_kind", "==", "Discount")], width=6),
    ]

    # Pharmacy — inventory health, expiry control, dispensing
    EXPIRING_SOON = adhoc("expiry_bucket", "IN", ["<= 30 days", "31-60 days", "61-90 days"])
    NOT_YET_EXPIRED_90 = [adhoc("days_until_expiry", ">=", 0), adhoc("days_until_expiry", "<=", 90)]
    IS_EXPIRED = adhoc("expiry_bucket", "==", "Expired")
    NEEDS_REORDER = adhoc("stock_status", "IN", ["Out of stock", "Below reorder", "Low (<20)"])
    charts += [
        # KPI strip
        big_number("KPI · Total Stock Value", "fct_drug_onhand", "Stock Value"),
        big_number("KPI · Value Expiring ≤90d", "fct_drug_stock", "Stock Value",
                   subheader="at risk", filters=[EXPIRING_SOON]),
        big_number("KPI · Expired Stock Value", "fct_drug_stock", "Stock Value",
                   subheader="quarantine / write-off", filters=[IS_EXPIRED]),
        big_number("KPI · Reorder Alerts", "fct_drug_onhand", "Reorder-alert SKUs",
                   subheader="out / below reorder / low"),
        # Expiry control — the about-to-expire worklist
        table_chart("Near-Expiry Medicines (≤90 days)", "fct_drug_stock",
                    ["drug_name", "lot_number", "location", "expiration_date",
                     "days_until_expiry", "quantity", "stock_value"],
                    row_limit=300, width=12, filters=NOT_YET_EXPIRED_90,
                    order_by=[("days_until_expiry", True)],
                    conditional_formatting=[
                        {"column": "days_until_expiry", "operator": "<", "targetValue": 30, "colorScheme": "#e04355"},
                        {"column": "days_until_expiry", "operator": "<", "targetValue": 60, "colorScheme": "#fcc700"},
                    ]),
        cat_bar_chart("Value at Risk by Expiry Window", "fct_drug_stock", "Stock Value",
                      "expiry_bucket", row_limit=10, width=6),
        table_chart("Expired Stock (quarantine)", "fct_drug_stock",
                    ["drug_name", "lot_number", "location", "expiration_date", "quantity", "stock_value"],
                    row_limit=300, width=6, filters=[IS_EXPIRED],
                    order_by=[("expiration_date", True)]),
        # Stock levels & valuation
        table_chart("Low / Out of Stock (reorder list)", "fct_drug_onhand",
                    ["drug_name", "drug_category", "on_hand", "unit_price", "stock_status"],
                    row_limit=300, width=12, filters=[NEEDS_REORDER],
                    order_by=[("on_hand", True)]),
        cat_bar_chart("Stock Value by Category", "fct_drug_onhand", "Stock Value",
                      "drug_category", row_limit=20, width=6),
        # Dispensing / prescribing
        cat_bar_chart("Top Prescribed Drugs", "fct_drug_orders", "Prescriptions", "drug_name",
                      row_limit=25, width=6),
        ts_chart("Prescription Trend", "fct_drug_orders", ["Prescriptions"], "ordered_at", width=6),
    ]

    # Inpatient / bed management
    charts += [
        big_number("KPI · Currently Admitted", "fct_bed_assignments", "Admissions"),
        ts_chart("Admissions Trend", "fct_bed_assignments", ["Admissions"], "assigned_at", groupby=["ward"]),
        cat_bar_chart("Bed Days by Ward", "fct_bed_assignments", "Bed Days", "ward"),
        pie_chart("Beds by Type", "fct_bed_assignments", "Admissions", "bed_type"),
    ]

    # ===================== NEW DASHBOARDS ============================== #

    # --- Inpatient Outcomes & Mortality (HMIS core) ---
    charts += [
        big_number("KPI · Inpatient Deaths", "fct_inpatient_outcomes", "Deaths"),
        big_number("KPI · Mortality Rate", "fct_inpatient_outcomes", "Mortality %", subheader="% of inpatient outcomes"),
        stacked_bar("Outcomes by Age Group", "fct_inpatient_outcomes", "Outcomes", "age_group", "outcome"),
        pie_chart("Outcome Mix", "fct_inpatient_outcomes", "Outcomes", "outcome"),
        ts_chart("Inpatient Deaths Trend", "fct_inpatient_outcomes", ["Deaths"], "outcome_at"),
        heatmap_chart("Outcome x Age Heatmap", "fct_inpatient_outcomes", "Outcomes", "age_group", "outcome"),
    ]

    # --- Maternal & Child Health (neonatal real; rest scaffolded) ---
    charts += [
        big_number("KPI · Neonatal Deaths", "fct_inpatient_outcomes", "Deaths",
                   subheader="age <= 28 days",
                   filters=[adhoc("neonatal", "==", "Neonate")]),
        pie_chart("Neonatal Deaths by Sex", "fct_inpatient_outcomes", "Deaths", "gender",
                  filters=[adhoc("neonatal", "==", "Neonate"), adhoc("outcome", "==", "Death")]),
        cat_bar_chart("Neonatal Deaths by Age Band", "fct_inpatient_outcomes", "Deaths", "age_group",
                      filters=[adhoc("neonatal", "==", "Neonate"), adhoc("outcome", "==", "Death")]),
        ts_chart("Neonatal Deaths Trend", "fct_inpatient_outcomes", ["Deaths"], "outcome_at",
                 viz="echarts_timeseries_line"),
    ]

    # --- Disease Surveillance ---
    charts += [
        ts_chart("Surveillance · Diagnosis Watch (weekly)", "fct_diagnoses", ["Diagnoses"], "diagnosed_at",
                 groupby=["diagnosis"], width=12),
        heatmap_chart("Diagnosis x Age Heatmap", "fct_diagnoses", "Diagnoses", "age_group", "diagnosis"),
        cat_bar_chart("Diagnoses by District", "fct_diagnoses", "Diagnoses", "district", row_limit=30),
        heatmap_chart("Diagnosis x District Heatmap", "fct_diagnoses", "Diagnoses", "district", "diagnosis"),
    ]

    # --- Equity & Free Care (payment proxy + scaffold) ---
    charts += [
        pie_chart("Invoices by Payment State", "fct_invoice_lines", "Invoices", "payment_state"),
        ts_chart("Billed vs Outstanding Trend", "fct_outstanding_ar", ["Billed", "Outstanding"], "invoiced_at"),
        cat_bar_chart("Outstanding by Aging", "fct_outstanding_ar", "Outstanding", "aging_bucket"),
    ]

    # --- Data Quality & Governance ---
    charts += [
        big_number("DQ · % Gender Complete", "dq_patient_quality", "% Gender Complete"),
        big_number("DQ · % Birthdate Complete", "dq_patient_quality", "% Birthdate Complete"),
        big_number("DQ · % Address Complete", "dq_patient_quality", "% Address Complete"),
        big_number("DQ · % Diagnoses Coded", "dq_diagnosis_coding", "% Coded"),
        ts_chart("Registration Completeness Trend", "dq_patient_quality",
                 ["% Gender Complete", "% Birthdate Complete", "% Address Complete"], "registered_at", width=12),
        ts_chart("Diagnosis Coding Trend", "dq_diagnosis_coding", ["% Coded"], "diagnosed_at"),
        cat_bar_chart("Voided Rate by Entity", "dq_voided_rates", "Voided %", "entity", row_limit=20),
        table_chart("Voided Counts", "dq_voided_rates", ["entity"],
                    metrics=["Voided", "Voided %"], row_limit=20),
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
            "public": True,
            "charts": ["Gender Distribution", "Age Distribution", "Top Municipalities",
                       "Patients by District", "Visits by Hour of Day", "Visits by Status"],
            "filters": [("fct_patients", "gender", "Gender"),
                        ("fct_patients", "age_group", "Age Group"),
                        ("fct_patients", "district", "District"),
                        ("fct_patients", "province", "Province")],
        },
        {
            "title": "NidanEHR · Clinical Operations",
            "roles": ["Clinical"],
            "charts": ["Encounter Types", "Provider Productivity", "Encounters by Location",
                       "Avg Length of Stay"],
            "filters": [("fct_encounters", "encounter_type", "Encounter Type"),
                        ("fct_encounters", "location", "Location"),
                        ("fct_encounters", "provider_name", "Provider")],
        },
        {
            "title": "NidanEHR · Morbidity & Public Health",
            "public": True,
            "charts": ["Top Diagnoses", "Morbidity by Age Group", "Morbidity by Gender",
                       "Diagnosis Trend"],
            "filters": [("fct_diagnoses", "diagnosis", "Diagnosis"),
                        ("fct_diagnoses", "age_group", "Age Group"),
                        ("fct_diagnoses", "gender", "Gender"),
                        ("fct_diagnoses", "dx_rank", "Diagnosis Rank")],
        },
        {
            "title": "NidanEHR · Laboratory Analytics",
            "roles": ["Lab"],
            "charts": ["Lab Test Volume", "Top Tests", "Turnaround Time Trend", "Tests by Status"],
            "filters": [("fct_lab_orders", "test_section", "Test Section"),
                        ("fct_lab_orders", "test_name", "Test"),
                        ("fct_lab_orders", "status", "Status")],
        },
        {
            "title": "NidanEHR · Financial Performance",
            "roles": ["Finance"],
            "charts": ["Revenue by Category Trend", "Revenue by Category", "Top Revenue Products",
                       "AR Aging", "Invoices by Payment State",
                       "Payment Method vs Amount", "Insurance · Claimed vs Settled",
                       "Insurance Claims by Status", "Discount vs Discount Provided"],
            "filters": [("fct_invoice_lines", "product_category", "Service Category"),
                        ("fct_invoice_lines", "payment_state", "Payment State"),
                        ("fct_pos_payments", "payment_method", "Payment Method"),
                        ("fct_insurance_claims", "insurance_type", "Insurance Type"),
                        ("fct_insurance_claims", "claim_status", "Claim Status")],
        },
        {
            "title": "NidanEHR · Pharmacy Management",
            "roles": ["Pharmacy"],
            "note": ("**Inventory health, expiry control & dispensing.** Expiry/stock data "
                     "comes from Odoo (product_expiry is installed). Charts are empty until "
                     "the pharmacy receives stock into internal locations **with lot + expiry "
                     "tracking enabled** and configures **reorder rules** (min qty) per drug."),
            "charts": ["KPI · Total Stock Value", "KPI · Value Expiring ≤90d",
                       "KPI · Expired Stock Value", "KPI · Reorder Alerts",
                       "Near-Expiry Medicines (≤90 days)", "Value at Risk by Expiry Window",
                       "Expired Stock (quarantine)", "Low / Out of Stock (reorder list)",
                       "Stock Value by Category", "Top Prescribed Drugs", "Prescription Trend"],
            "filters": [("fct_drug_stock", "drug_name", "Drug"),
                        ("fct_drug_stock", "drug_category", "Category"),
                        ("fct_drug_stock", "expiry_bucket", "Expiry Window"),
                        ("fct_drug_stock", "location", "Location"),
                        ("fct_drug_onhand", "stock_status", "Stock Status")],
        },
        # ── Dedicated low stock monitor
        {
            "title": "NidanEHR · Stock Reorder Monitor",
            "roles": ["Pharmacy"],
            "charts": [
                "Low / Out of Stock (reorder list)",
            ],
            "filters": [
                ("fct_drug_onhand", "drug_name",     "Drug"),
                ("fct_drug_onhand", "drug_category", "Category"),
                ("fct_drug_onhand", "stock_status",  "Stock Status"),
            ],
        },
        {
            "title": "NidanEHR · Inpatient & Bed Management",
            "roles": ["Clinical"],
            "charts": ["KPI · Currently Admitted", "Admissions Trend", "Bed Days by Ward", "Beds by Type"],
            "filters": [("fct_bed_assignments", "ward", "Ward"),
                        ("fct_bed_assignments", "bed_type", "Bed Type"),
                        ("fct_bed_assignments", "bed_status", "Bed Status")],
        },

        # ===================== NEW DASHBOARDS ============================= #
        {
            "title": "NidanEHR · Inpatient Outcomes & Mortality",
            "roles": ["Clinical"],
            "note": ("**Source:** discharge-outcome `obs` (same concept the federal HMIS "
                     "Hospital Indoor report uses). If outcomes look empty, verify the "
                     "outcome concept's answer names against `fct_inpatient_outcomes` SQL."),
            "charts": ["KPI · Inpatient Deaths", "KPI · Mortality Rate", "Outcome Mix",
                       "Outcomes by Age Group", "Outcome x Age Heatmap", "Inpatient Deaths Trend"],
            "filters": [("fct_inpatient_outcomes", "outcome", "Outcome"),
                        ("fct_inpatient_outcomes", "age_group", "Age Group"),
                        ("fct_inpatient_outcomes", "gender", "Gender"),
                        ("fct_inpatient_outcomes", "neonatal", "Neonatal")],
        },
        {
            "title": "NidanEHR · Maternal & Child Health",
            "roles": ["Clinical"],
            "note": ("**Neonatal mortality is live** (from discharge outcomes). "
                     "⚠️ _Pending data capture for full MNCH:_ ANC visits, deliveries & mode, "
                     "PNC, immunization, and family planning require coded `obs`/encounter types "
                     "to be mapped — add datasets once those concepts are confirmed."),
            "charts": ["KPI · Neonatal Deaths", "Neonatal Deaths by Sex",
                       "Neonatal Deaths by Age Band", "Neonatal Deaths Trend"],
            "filters": [("fct_inpatient_outcomes", "gender", "Gender"),
                        ("fct_inpatient_outcomes", "age_group", "Age Band")],
        },
        {
            "title": "NidanEHR · Disease Surveillance",
            "public": True,
            "note": ("Use the **Diagnosis** filter as a watchlist (multi-select the diseases to "
                     "monitor), then set **Time Grain = Week**. ⚠️ _A notifiable/communicable "
                     "disease classification (ICD grouping) is not yet mapped_ — add it to enable "
                     "automatic communicable-vs-NCD splits and outbreak thresholds."),
            "charts": ["Surveillance · Diagnosis Watch (weekly)", "Diagnosis x Age Heatmap",
                       "Diagnoses by District", "Diagnosis x District Heatmap"],
            "filters": [("fct_diagnoses", "diagnosis", "Diagnosis (watchlist)"),
                        ("fct_diagnoses", "district", "District"),
                        ("fct_diagnoses", "age_group", "Age Group"),
                        ("fct_diagnoses", "gender", "Gender")],
        },
        {
            "title": "NidanEHR · Equity & Free Care",
            "roles": ["Finance"],
            "note": ("⚠️ _Scaffold using billing payment-state as a proxy._ The federal HMIS "
                     "requires beneficiary-category reporting (Ultra-poor, FCHV, Senior >60, "
                     "Disabled, GBV, Helpless) and **amount of cost exempted**. These need a "
                     "coded free-care / payer category captured at registration or billing, plus "
                     "the reporting mart to join clinical ↔ billing. Charts here are placeholders "
                     "until that field exists."),
            "charts": ["Invoices by Payment State", "Billed vs Outstanding Trend",
                       "Outstanding by Aging"],
            "filters": [("fct_invoice_lines", "payment_state", "Payment State"),
                        ("fct_outstanding_ar", "aging_bucket", "Aging Bucket")],
        },
        {
            "title": "NidanEHR · Data Quality & Governance",
            "note": ("Monitors capture quality — reports are only as good as the data behind them. "
                     "Track completeness, diagnosis coding, and voided rates over time."),
            "charts": ["DQ · % Gender Complete", "DQ · % Birthdate Complete",
                       "DQ · % Address Complete", "DQ · % Diagnoses Coded",
                       "Registration Completeness Trend", "Diagnosis Coding Trend",
                       "Voided Rate by Entity", "Voided Counts"],
            "filters": [],
        },
    ]


# --------------------------------------------------------------------------- #
# Layout + native-filter JSON generators
# --------------------------------------------------------------------------- #
def _hash_id(prefix, *parts):
    h = hashlib.md5("::".join(str(p) for p in parts).encode()).hexdigest()[:10]
    return f"{prefix}-{h}"


def build_position_json(title, slices, note=None):
    """Grid layout: optional markdown banner, KPI row, then 2-per-row charts."""
    position = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"type": "HEADER", "id": "HEADER_ID", "meta": {"text": title}},
    }
    rows = []

    if note:
        md_row = _hash_id("ROW", title, "noterow")
        md_id = _hash_id("MARKDOWN", title, "note")
        position[md_id] = {
            "type": "MARKDOWN", "id": md_id, "children": [],
            "meta": {"width": 12, "height": 18, "code": note},
            "parents": ["ROOT_ID", "GRID_ID", md_row],
        }
        position[md_row] = {
            "type": "ROW", "id": md_row, "children": [md_id],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
            "parents": ["ROOT_ID", "GRID_ID"],
        }
        rows.append(md_row)
    kpis = [s for s in slices if s.viz_type == "big_number_total"]
    others = [s for s in slices if s.viz_type != "big_number_total"]

    fallback_w = {"big_number_total": 3, "pie": 4, "heatmap_v2": 6, "table": 6}

    def width_for(s):
        w = CHART_WIDTHS.get(s.slice_name) or fallback_w.get(s.viz_type, 6)
        return max(2, min(12, w))

    def height_for(width, is_kpi):
        if is_kpi:
            return 38
        if width >= 12:
            return 80   # full-width charts get more vertical room
        if width >= 6:
            return 52
        return 48

    def add_row(members):
        """members: list of (slice, width)."""
        if not members:
            return
        row_id = _hash_id("ROW", title, len(rows))
        is_kpi = all(s.viz_type == "big_number_total" for s, _ in members)
        row_h = max(height_for(w, is_kpi) for _, w in members)
        children = []
        for s, w in members:
            cid = _hash_id("CHART", s.id)
            position[cid] = {
                "type": "CHART", "id": cid, "children": [],
                "meta": {"chartId": s.id, "sliceName": s.slice_name,
                         "width": w, "height": row_h},
                "parents": ["ROOT_ID", "GRID_ID", row_id],
            }
            children.append(cid)
        position[row_id] = {
            "type": "ROW", "id": row_id, "children": children,
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
            "parents": ["ROOT_ID", "GRID_ID"],
        }
        rows.append(row_id)

    def pack(items):
        """Greedy bin-packing into 12-col rows -> mixed full/3-col/2-col rows."""
        row, used = [], 0
        for s in items:
            w = width_for(s)
            if row and used + w > 12:
                add_row(row)
                row, used = [], 0
            row.append((s, w))
            used += w
        add_row(row)

    pack(kpis)     # KPI strip(s) first
    pack(others)   # then everything else, packed by width

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

        # --- Step 4a: RBAC roles ------------------------------------------ #
        # Custom hospital roles cloned from Gamma's base perms; per-dashboard
        # access is then granted via the DASHBOARD_RBAC feature flag by
        # attaching roles to dashboards (below). Admin bypasses RBAC (sees all).
        print("\n[4/4] RBAC roles + dashboards + native filters")
        from superset.extensions import security_manager as sm

        STAFF_ROLES = ["Clinical", "Lab", "Pharmacy", "Finance"]
        PUBLIC_ROLE_NAMES = ["Public"] + STAFF_ROLES  # public dashboards: everyone

        role_cache = {}

        def get_role(name):
            if name in role_cache:
                return role_cache[name]
            role = sm.find_role(name)
            if not role:
                role = sm.add_role(name)
            # Clone Gamma's base permissions into custom staff roles so they can
            # log in and render dashboards (Public is handled by PUBLIC_ROLE_LIKE).
            if name in STAFF_ROLES:
                gamma = sm.find_role("Gamma")
                if gamma:
                    role.permissions = list(gamma.permissions)
            db.session.commit()
            role_cache[name] = role
            return role

        for rn in STAFF_ROLES:
            get_role(rn)
        print(f"  ✓ roles ready: {', '.join(['Admin'] + STAFF_ROLES + ['Public'])}")

        def roles_for(spec):
            if spec.get("public"):
                names = PUBLIC_ROLE_NAMES
            elif spec.get("roles"):
                names = spec["roles"]
            else:
                names = []  # admin-only (no roles attached -> only Admin sees it)
            return [get_role(n) for n in names]

        def upsert_dashboard(spec):
            title = spec["title"]
            slices = [charts[n] for n in spec["charts"] if n in charts]
            if not slices:
                print(f"  ⚠ {title}: no charts available, skipping")
                return
            primary_ds_id = slices[0].datasource_id
            position = build_position_json(title, slices, note=spec.get("note"))
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
            roles = roles_for(spec)
            dash.roles = roles
            db.session.commit()
            access = ("Admin only" if not roles
                      else ("Public + staff" if spec.get("public")
                            else "+".join(r.name for r in roles)))
            print(f"  ✓ {title} ({len(slices)} charts, {len(native_filters)} filters) — {access}")

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
