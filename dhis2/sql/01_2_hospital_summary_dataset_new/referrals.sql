-- ==============================================================
-- Dataset : 01-2 Hospital Summary Dataset (NEW)
-- Group   : Referrals
-- Params  : :start_date  :end_date
-- ==============================================================
-- TODO: Replace NULL with actual SQL expressions

SELECT
  NULL AS referred_in_cases_at_hospital_f,  -- Referred In Cases at the Hospital / Female
  NULL AS referred_in_cases_at_hospital_m,  -- Referred In Cases at the Hospital / Male
  NULL AS opd_patients_referred_out_from_hospital_f,  -- OPD Patients Referred Out from the Hospital / Female
  NULL AS opd_patients_referred_out_from_hospital_m,  -- OPD Patients Referred Out from the Hospital / Male
  NULL AS ipd_patients_referred_out_from_hospital_f,  -- IPD Patients Referred Out from the Hospital / Female
  NULL AS ipd_patients_referred_out_from_hospital_m,  -- IPD Patients Referred Out from the Hospital / Male
  NULL AS emergency_patients_referred_out_from_hospital_f,  -- Emergency Patients Referred Out from the Hospital / Female
  NULL AS emergency_patients_referred_out_from_hospital_m   -- Emergency Patients Referred Out from the Hospital / Male

FROM
  -- TODO: replace with your actual source table(s)
  encounter e
  JOIN encounter_type et ON et.encounter_type_id = e.encounter_type

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
