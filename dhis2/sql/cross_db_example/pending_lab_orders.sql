-- =============================================================================
-- Dataset  : Lab-OPD Integrated Summary
-- Group    : Pending Lab Orders
-- Source   : OpenELIS only (PostgreSQL) — single-DB, still using DuckDB conn
-- Params   : :start_date  :end_date
-- =============================================================================
--
-- Even though this query only touches OpenELIS, it still runs through the
-- DuckDB connection because the program's source_db is "duckdb".
-- You can mix single-DB queries and cross-DB queries within the same program.
-- =============================================================================

SELECT
  COUNT(a.id) AS pending_lab_tests

FROM
  openelis.clinlims.sample   AS s
  JOIN openelis.clinlims.analysis AS a ON a.sampitem_id = s.id

WHERE
  s.entered_date BETWEEN :start_date AND :end_date
  AND a.status NOT IN ('RR', 'CA')   -- Not yet released or cancelled
  AND s.status  NOT IN ('RR', 'CA')
