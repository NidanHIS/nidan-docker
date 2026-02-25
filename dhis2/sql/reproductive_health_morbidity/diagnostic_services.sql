-- ==============================================================
-- Dataset : 09 Reproductive Health Morbidity Service (NEW)
-- Group   : Diagnostic Services
-- Params  : :start_date  :end_date
-- ==============================================================
-- TODO: Replace NULL with actual SQL expressions

SELECT
  NULL AS suspected_40_70,  -- RH&Morbidity-Breast Cancer-Suspected / 40 to 70 Years
  NULL AS suspected_40,  -- RH&Morbidity-Breast Cancer-Suspected / < 40 Years
  NULL AS suspected_70_plus,  -- RH&Morbidity-Breast Cancer-Suspected / > 70 Years
  NULL AS suspected   -- RH&Morbidity-Obstetric Fistula-Suspected

FROM
  -- TODO: replace with your actual source table(s)
  encounter e
  JOIN encounter_type et ON et.encounter_type_id = e.encounter_type

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
