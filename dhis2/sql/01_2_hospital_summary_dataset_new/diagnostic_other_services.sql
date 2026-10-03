-- ==============================================================
-- Dataset : 01-2 Hospital Summary Dataset (NEW)
-- Group   : Diagnostic/Other Services
-- Params  : :start_date  :end_date
-- ==============================================================
-- TODO: Replace NULL with actual SQL expressions

SELECT
  NULL AS total_x_rays_done,  -- Total X-Rays Done
  NULL AS total_ultrasonogram_tests_done,  -- Total Ultrasonogram (USG) Tests Done
  NULL AS total_echocardiogram_tests_done,  -- Total Echocardiogram (Echo) Tests Done
  NULL AS total_electro_encephalogram_tests_done,  -- Total Electro Encephalogram (EEG) Tests Done
  NULL AS total_electrocardiogram_tests_done,  -- Total Electrocardiogram (ECG) Tests Done
  NULL AS total_treadmill_test_done,  -- Total Treadmill Test Done
  NULL AS total_computed_tomographic_scans_done,  -- Total Computed Tomographic (CT) Scans Done
  NULL AS total_magnetic_resonance_imaging_tests_done,  -- Total Magnetic Resonance Imaging (MRI) Tests Done
  NULL AS total_endoscopy_tests_done,  -- Total Endoscopy Tests Done
  NULL AS total_colonoscopy_tests_done,  -- Total Colonoscopy Tests Done
  NULL AS total_bronchoscopy_done,  -- Total Bronchoscopy Done
  NULL AS total_nuclear_medicine_investigations_done,  -- Total Nuclear Medicine Investigations Done
  NULL AS mammogram,  -- Mammogram
  NULL AS cystoscopy,  -- Cystoscopy
  NULL AS dexa_scan,  -- DEXA Scan
  NULL AS dtpa_scan,  -- DTPA Scan
  NULL AS electroconvulsive_therapy,  -- Electroconvulsive Therapy (ECT)
  NULL AS transcranial_magnetic_simulation,  -- Transcranial Magnetic Simulation (TMS)
  NULL AS hospital_diagnostic_services_total_laboratory_person,  -- Hospital- Diagnostic Services- Total Laboratory service Provided to Person
  NULL AS hospital_diagnostic_services_other_service_provided_person   -- Hospital- Diagnostic Services- Other Service Provided if any to Person

FROM
  -- TODO: replace with your actual source table(s)
  encounter e
  JOIN encounter_type et ON et.encounter_type_id = e.encounter_type

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
