# NidanEHR Superset — Reporting & Analytics

Configurable, filterable hospital analytics over OpenMRS (clinical), OpenELIS
(lab) and Odoo (billing/pharmacy).

## Reporting architecture

The reporting layer is **code-seeded** at container start by
`init_superset_metadata.py` (idempotent — re-runs on every boot, creating what's
missing and updating SQL / metrics / chart params / dashboard filters in place).

The design has two principles:

### 1. Wide fact datasets (not pre-aggregated reports)

Each dataset is **one row per business event** and carries:

- a single real **datetime column** → Superset rolls it up to Day / Week /
  Month / Quarter / Year on demand;
- **every dimension preserved** (visit type, ward, gender, age group, provider,
  service category, payer, …) → filterable;
- additive base measures **+ saved metrics** for distinct counts (so
  `COUNT(DISTINCT patient)` stays correct at any grain).

There are **no hardcoded time windows** (`WHERE date >= NOW()-90d`) and **no
`LIMIT`** baked into dataset SQL — those defeat dashboard filtering. Filtering is
done in Superset, not in the SQL.

| Dataset | Source | Grain |
|---|---|---|
| `fct_patients` | OpenMRS | patient (registration) |
| `fct_visits` | OpenMRS | visit |
| `fct_encounters` | OpenMRS | encounter × provider |
| `fct_diagnoses` | OpenMRS | diagnosis (morbidity/surveillance) |
| `fct_drug_orders` | OpenMRS | prescription order |
| `fct_bed_assignments` | OpenMRS (bedmanagement) | bed assignment |
| `fct_lab_orders` | OpenELIS | analysis (test) |
| `fct_invoice_lines` | Odoo | invoice line (revenue) |
| `fct_outstanding_ar` | Odoo | open invoice (AR aging) |
| `fct_drug_stock` | Odoo | stock by lot (expiry + value, snapshot) |
| `fct_drug_onhand` | Odoo | stock by product vs reorder level (snapshot) |
| `fct_inpatient_outcomes` | OpenMRS | discharge-outcome obs (age/sex banded) |
| `dq_patient_quality` | OpenMRS | registration completeness flags |
| `dq_diagnosis_coding` | OpenMRS | coded vs uncoded diagnoses |
| `dq_voided_rates` | OpenMRS | voided record rates by entity |

### 2. Universal filters on every dashboard

Each dashboard is generated with native filters wired to all of its charts:

- **Time Range** — presets (Today, This week/month/quarter/year, Last N days) +
  **custom range**;
- **Time Grain** — Day / Week / Month / Quarter / Year selector;
- **one value filter per data element** declared for that dashboard;
- cross-filtering enabled, with a real grid layout.

Adding a new filter = add a `(table, column, label)` tuple to the dashboard's
`filters` list in `build_dashboard_specs()`. Adding a new report = add a dataset
spec + chart spec + reference it from a dashboard.

## Dashboards

| Dashboard | Focus |
|---|---|
| Executive Overview | KPIs + registrations/visits/lab/revenue trends |
| Patient Flow & Demographics | gender, age, geography, visit timing |
| Clinical Operations | encounters, provider productivity, LOS |
| Morbidity & Public Health | top diagnoses, age/sex-disaggregated morbidity |
| Laboratory Analytics | volume, turnaround time, status |
| Financial Performance | revenue by category/product, AR aging, payment state |
| Pharmacy Management | near-expiry & expired worklists, value-at-risk, low/out-of-stock vs reorder, valuation, top drugs, prescription trend |
| Inpatient & Bed Management | admissions, bed days/ward, bed types |
| **Inpatient Outcomes & Mortality** | outcome mix, age×sex, mortality rate, deaths trend (HMIS) |
| **Maternal & Child Health** | neonatal mortality live; ANC/delivery/immunization scaffolded |
| **Disease Surveillance** | diagnosis watchlist (weekly), age & district heatmaps |
| **Equity & Free Care** | payment-state proxy; beneficiary-category scaffolded |
| **Data Quality & Governance** | completeness, diagnosis coding, voided rates |

Dashboards marked _scaffold_ carry a markdown banner naming the data-capture
dependency that must be satisfied before they become fully live (see Roadmap).
New datasets backing these: `fct_inpatient_outcomes` (discharge-outcome obs),
`dq_patient_quality`, `dq_diagnosis_coding`, `dq_voided_rates`.

## Access control (role-based dashboards)

`DASHBOARD_RBAC` is enabled. Each dashboard is seeded with the roles allowed to
see it; **Admin bypasses RBAC and sees everything**. Roles `Clinical`, `Lab`,
`Pharmacy`, `Finance` are auto-created (cloned from Gamma's base permissions);
`Public` is the anonymous (not-logged-in) role.

| Dashboard | Visible to |
|---|---|
| Patient Flow & Demographics · Morbidity & Public Health · Disease Surveillance | **Public** (anyone, incl. not logged in) + all staff |
| Laboratory Analytics | Lab |
| Pharmacy Management | Pharmacy |
| Clinical Operations · Inpatient & Bed Mgmt · Inpatient Outcomes · MNCH | Clinical |
| Financial Performance · Equity & Free Care | Finance |
| Executive Overview · Data Quality & Governance | Admin only |

So a lab user (role `Lab`) sees the public boards **plus** Laboratory Analytics;
pharmacy sees public **plus** Pharmacy; etc. — exactly the layering requested.

**Assigning people to roles:** Settings → List Users → edit user → set role(s),
or map Keycloak groups → Superset roles once SSO is wired. To change a
dashboard's access, edit its `roles`/`public` tag in `build_dashboard_specs()`
and re-seed (or set roles in the dashboard's *Edit properties* dialog).

> Security note: `PUBLIC_ROLE_LIKE = "Gamma"` lets anonymous users render the
> public dashboards. If Superset is exposed to the public internet, harden the
> `Public` role (keep dashboard-view perms, drop chart/dataset *list* menu perms).

## Theme & layout

- **Light mode is forced** (`THEME_DARK = None`, theme admin UI disabled) — no
  dark toggle, appropriate for clinical/monitor displays.
- Dashboards use **width-based row packing** (not a fixed 2-column grid): KPIs in
  a top strip, then a greedy mix of full-width / 3-col / 2-col rows. Per-chart
  width is set in the chart helpers (`width=` arg, 12-col grid).

## Public display / embedding

Public dashboards can be shown on a lobby monitor or embedded in the hospital
website (same origin, via the `/superset` gateway path) — no login needed:

```
http://<host>/superset/dashboard/patient-flow-and-demographics/?standalone=1
```

`standalone=1` renders chromeless (no top nav); add `&standalone=3` to also hide
the filter bar for a clean kiosk view. Set an auto-refresh interval from the
dashboard's *⋯ → Set auto-refresh interval* menu for live monitors.

## Database connections (from env vars)

| Connection | Dialect | Env prefix |
|---|---|---|
| OpenMRS | MySQL/MariaDB* | `OPENMRS_DB_*` (`OPENMRS_DB_TYPE=mysql`) |
| OpenELIS | PostgreSQL | `OPENELIS_DB_*` |
| Odoo | PostgreSQL | `ODOO_DB_*` |

\* OpenMRS SQL in the seed script is **MySQL-dialect** on this branch. When
OpenMRS migrates to PostgreSQL (`feat/postgres`), set
`OPENMRS_DB_TYPE=postgresql` **and** port the OpenMRS dataset SQL
(`DATE_SUB`/`TIMESTAMPDIFF`/`CURDATE`/`HOUR` → Postgres equivalents).

## Structure

```
superset/
├── superset_config.py          # Flask/Superset config (proxy prefix, CORS, cache)
├── init_superset_metadata.py   # SOURCE OF TRUTH: datasets, charts, dashboards, filters
├── docker-entrypoint.sh        # migrations → admin → init → gunicorn
└── metadata/databases/*.yaml   # reference only (connections); not executed
```

## Access

- App: http://localhost/superset/
- Dashboards: http://localhost/superset/dashboard/list/
- SQL Lab: http://localhost/superset/sqllab/

## Roadmap / known gaps

Tracked follow-ups beyond the current "wide views + universal filters" baseline:

- **Reporting mart (hybrid → phase 2):** nightly ETL of all three sources into a
  dedicated Postgres star schema. Unlocks **cross-system reports** (revenue per
  visit type, lab cost per patient — impossible today across 3 connections),
  isolates analytics load from the live clinical DBs, and unifies the SQL
  dialect.
- **Nepali calendar (BS):** a `dim_date` carrying both AD and BS
  year/month/quarter and the Shrawan-start fiscal year, so monthly/quarterly/
  yearly reports align to HMIS/DHIS2 periods (reuse `nidan-dhis2-integration`
  BS↔AD logic).
- **Scheduling & alerts:** add Redis + Celery worker/beat to enable async
  queries, result caching, scheduled email report delivery, and threshold
  alerts.
- **Security/governance:** fail on default `SUPERSET_SECRET_KEY`, tighten CORS,
  Keycloak SSO + role mapping (replace local admin), Row-Level Security per
  facility/ward.
- **Schema validation:** `fct_diagnoses` (encounter_diagnosis) and
  `fct_bed_assignments` (bedmanagement) column names are best-effort and should
  be verified against the live schemas; they degrade to zero rows if absent.
