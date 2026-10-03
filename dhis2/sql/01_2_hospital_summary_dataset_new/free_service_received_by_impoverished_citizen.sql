-- ==============================================================
-- Dataset : 01-2 Hospital Summary Dataset (NEW)
-- Group   : Free Service Received by Impoverished Citizen
-- Params  : :start_date  :end_date
-- ==============================================================
-- TODO: Replace NULL with actual SQL expressions

SELECT
  NULL AS heart_ailments_f,  -- Ultra Poor Patients Received Free Services for Heart Ailments / Female
  NULL AS heart_ailments_m,  -- Ultra Poor Patients Received Free Services for Heart Ailments / Male
  NULL AS kidney_ailments_f,  -- Ultra Poor Patients Received Free Services for Kidney Ailments / Female
  NULL AS kidney_ailments_m,  -- Ultra Poor Patients Received Free Services for Kidney Ailments / Male
  NULL AS cancer_treatment_f,  -- Ultra Poor PatientsReceived Free Services  for Cancer Treatment / Female
  NULL AS cancer_treatment_m,  -- Ultra Poor PatientsReceived Free Services  for Cancer Treatment / Male
  NULL AS head_injury_f,  -- Ultra Poor Patients Received Free Services for Head Injury / Female
  NULL AS head_injury_m,  -- Ultra Poor Patients Received Free Services for Head Injury / Male
  NULL AS spinal_and_head_injury_f,  -- Ultra Poor Patients Received Free Services for Spinal and Head Injury / Female
  NULL AS spinal_and_head_injury_m,  -- Ultra Poor Patients Received Free Services for Spinal and Head Injury / Male
  NULL AS alzheimer_parkinson_disease_f,  -- Ultra Poor Patients Received Free Services for Alzheimer/Parkinson Disease / Female
  NULL AS alzheimer_parkinson_disease_m,  -- Ultra Poor Patients Received Free Services for Alzheimer/Parkinson Disease / Male
  NULL AS parkinson_f,  -- Ultra Poor Patients Received Free Services for Parkinson / Female
  NULL AS parkinson_m,  -- Ultra Poor Patients Received Free Services for Parkinson / Male
  NULL AS sickle_cell_anaemia_f,  -- Ultra Poor Patients Received Free Services for Sickle Cell Anaemia / Female
  NULL AS sickle_cell_anaemia_m   -- Ultra Poor Patients Received Free Services for Sickle Cell Anaemia / Male

FROM
  -- TODO: replace with your actual source table(s)
  encounter e
  JOIN encounter_type et ON et.encounter_type_id = e.encounter_type

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
