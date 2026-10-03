-- ==============================================================
-- Dataset : 01-2 Hospital Summary Dataset (NEW)
-- Group   : Sanctioned bed
-- Params  : :start_date  :end_date
-- ==============================================================
-- TODO: Replace NULL with actual SQL expressions

SELECT
  NULL AS number_of_sanctioned_beds_in_hospital,  -- Number of Sanctioned Beds in Hospital
  NULL AS number_of_operational_beds_in_hospital,  -- Number of Operational Beds in Hospital
  NULL AS number_of_emergency_beds_in_hospital,  -- Number of Emergency Beds in Hospital
  NULL AS total_number_of_patients_admitted_in_hospital,  -- Total Number of Patients admitted in Hospital
  NULL AS total_in_patient_days   -- Total In-Patient  Days

FROM
  -- TODO: replace with your actual source table(s)
  encounter e
  JOIN encounter_type et ON et.encounter_type_id = e.encounter_type

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
