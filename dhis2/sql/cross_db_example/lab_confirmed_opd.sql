-- =============================================================================
-- Dataset  : Lab-OPD Integrated Summary
-- Group    : Lab-Confirmed OPD Visits
-- Cross-DB : OpenMRS (MySQL)  ×  OpenELIS (PostgreSQL) via DuckDB
-- Params   : :start_date  :end_date
-- =============================================================================
--
-- HOW DuckDB CROSS-DB WORKS
-- ─────────────────────────
-- When source_db = "duckdb", the engine calls get_duckdb_connection() which
-- runs two ATTACH statements before yielding the connection:
--
--   ATTACH 'mysql+pymysql://...' AS openmrs  (TYPE mysql)
--   ATTACH 'postgresql://...'   AS openelis  (TYPE postgres)
--
-- Every table in this SQL must be fully qualified:
--   openmrs.<schema>.<table>     e.g.  openmrs.openmrs.patient_identifier
--   openelis.<schema>.<table>    e.g.  openelis.clinlims.result
--
-- DuckDB pushes as much of the filtering as possible down to each remote DB
-- (predicate pushdown) so only rows matching the WHERE clause travel over
-- the network.
--
-- PARAMETER BINDING
-- ─────────────────
-- DuckDB does NOT support SQLAlchemy named-param style.
-- Parameters are injected as Python format strings by the engine.
-- In the query below we use $start_date / $end_date DuckDB positional
-- params — see engine/source_db.py execute_duckdb for how they are bound.
-- (For simplicity this example uses literal substitution via Python f-string
-- in the _execute_duckdb helper added in source_db.py.)
-- =============================================================================

SELECT

  -- ── Total OPD encounters that have at least one lab result ──────────────
  COUNT(DISTINCT e.encounter_id)                          AS total_opd_lab_confirmed,

  -- ── Malaria breakdowns using CASE ───────────────────────────────────────
  COUNT(DISTINCT CASE
    WHEN obs_concept.concept_id IN (
            -- Concept UIDs for "Malaria, confirmed" (Plasmodium spp.)
            -- Replace with real OpenMRS concept IDs for your deployment
            82, 143, 1371
         )
    AND pp.gender = 'M'
    THEN e.encounter_id
  END)                                                    AS malaria_lab_confirmed_male,

  COUNT(DISTINCT CASE
    WHEN obs_concept.concept_id IN (82, 143, 1371)
    AND pp.gender = 'F'
    THEN e.encounter_id
  END)                                                    AS malaria_lab_confirmed_female,

  -- ── Tuberculosis (concept 42 = confirmed TB in standard OpenMRS dict) ───
  COUNT(DISTINCT CASE
    WHEN obs_concept.concept_id = 42
    THEN e.encounter_id
  END)                                                    AS tb_lab_confirmed

FROM
  -- ── OpenMRS tables (MySQL) ───────────────────────────────────────────────
  openmrs.openmrs.encounter          AS e
  JOIN openmrs.openmrs.patient       AS p  ON p.patient_id = e.patient_id
  JOIN openmrs.openmrs.person       AS pp  ON pp.person_id = e.patient_id
  JOIN openmrs.openmrs.obs           AS o  ON o.encounter_id = e.encounter_id
                                          AND o.voided = 0
  JOIN openmrs.openmrs.concept       AS obs_concept ON obs_concept.concept_id = o.concept_id
  JOIN openmrs.openmrs.encounter_type AS et ON et.encounter_type_id = e.encounter_type

  -- ── Match to OpenELIS result via patient national ID ────────────────────
  JOIN openmrs.openmrs.patient_identifier AS pi
       ON pi.patient_id = e.patient_id
       AND pi.identifier_type = 3     -- 3 = National ID in most Bahmni deployments
       AND pi.voided = 0

  JOIN openelis.clinlims.patient          AS ep
       ON ep.national_id = pi.identifier

  -- In OpenELIS the patient→sample link is via the sample_human junction table:
  --   clinlims.sample_human(id, samp_id, patient_id)
  -- clinlims.sample does NOT have a patient_id column directly.
  JOIN openelis.clinlims.sample_human     AS sh
       ON sh.patient_id = ep.id

  JOIN openelis.clinlims.sample           AS s
       ON s.id = sh.samp_id
       AND s.entered_date BETWEEN :start_date AND :end_date
       AND s.status = 'RR'

  -- analysis links to sample via sample_item (one sample can have many items)
  JOIN openelis.clinlims.sample_item      AS si
       ON si.samp_id = s.id

  JOIN openelis.clinlims.analysis         AS a
       ON a.sampitem_id = si.id
       AND a.status = 'RR'

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
  AND et.name IN ('OPD', 'Outpatient Initial', 'Outpatient Follow-up')
  -- Limit to diagnoses only (coded observation group)
  AND o.concept_id IN (82, 143, 1371, 42)
