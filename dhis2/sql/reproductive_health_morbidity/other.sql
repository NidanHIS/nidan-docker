-- ==============================================================
-- Dataset : 09 Reproductive Health Morbidity Service (NEW)
-- Group   : Other
-- Params  : :start_date  :end_date
-- ==============================================================
-- TODO: Replace NULL with actual SQL expressions

SELECT
  NULL AS breast_cancer_40_70,  -- RH Morbidity - Breast Cancer / 40 to 70 Years
  NULL AS breast_cancer_40,  -- RH Morbidity - Breast Cancer / < 40 Years
  NULL AS breast_cancer_70_plus,  -- RH Morbidity - Breast Cancer / > 70 Years
  NULL AS surgery,  -- RH&Morbidity-Obstetric Fistula-Surgery
  NULL AS positive_hpvdna,  -- RH&Morbidity-Cervical Cancer-Womens (30-49 Years)-Positive / HPVDNA
  NULL AS positive_pap_smear_other,  -- RH&Morbidity-Cervical Cancer-Womens (30-49 Years)-Positive / PAP Smear & Other
  NULL AS positive_via,  -- RH&Morbidity-Cervical Cancer-Womens (30-49 Years)-Positive / VIA
  NULL AS positive_hpvdna_2,  -- RH&Morbidity-Cervical Cancer-Womens (50+ Years)-Positive / HPVDNA
  NULL AS positive_pap_smear_other_2,  -- RH&Morbidity-Cervical Cancer-Womens (50+ Years)-Positive / PAP Smear & Other
  NULL AS positive_via_2,  -- RH&Morbidity-Cervical Cancer-Womens (50+ Years)-Positive / VIA
  NULL AS screened,  -- RH&Morbidity-Obstetric Fistula-Screened
  NULL AS referred,  -- RH&Morbidity-Pelvic Organ Prolapse-Referred
  NULL AS screened_2,  -- RH&Morbidity-Pelvic Organ Prolapse-Screened
  NULL AS performed,  -- RH&Morbidity-Colposcopy-Performed
  NULL AS performed_2,  -- RH Morbidity - Leep Method - Performed
  NULL AS surgery_2,  -- RH&Morbidity-Pelvic Organ Prolapse-Surgery
  NULL AS screened_40_70,  -- RH&Morbidity-Breast Cancer-Screened / 40 to 70 Years
  NULL AS screened_40,  -- RH&Morbidity-Breast Cancer-Screened / < 40 Years
  NULL AS screened_70_plus,  -- RH&Morbidity-Breast Cancer-Screened / > 70 Years
  NULL AS identified_stage_1_2,  -- RH&Morbidity-Pelvic Organ Prolapse-Prolapsed - Identified / Stage 1 & 2
  NULL AS identified_stage_3,  -- RH&Morbidity-Pelvic Organ Prolapse-Prolapsed - Identified / Stage 3
  NULL AS identified_stage_4,  -- RH&Morbidity-Pelvic Organ Prolapse-Prolapsed - Identified / Stage 4
  NULL AS received,  -- RH&Morbidity-Ablativ Treatment-Received
  NULL AS referred_2,  -- RH&Morbidity-Obstetric Fistula-Referred
  NULL AS screened_hpvdna,  -- RH&Morbidity-Cervical Cancer-Womens (50+ Years)-Screened / HPVDNA
  NULL AS screened_pap_smear_other,  -- RH&Morbidity-Cervical Cancer-Womens (50+ Years)-Screened / PAP Smear & Other
  NULL AS screened_via,  -- RH&Morbidity-Cervical Cancer-Womens (50+ Years)-Screened / VIA
  NULL AS ring_pessary_applied,  -- RH&Morbidity-Pelvic Organ Prolapse-Ring Pessary Applied
  NULL AS screened_hpvdna_2,  -- RH&Morbidity-Cervical Cancer-Womens (30-49 Years)-Screened / HPVDNA
  NULL AS screened_pap_smear_other_2,  -- RH&Morbidity-Cervical Cancer-Womens (30-49 Years)-Screened / PAP Smear & Other
  NULL AS screened_via_2   -- RH&Morbidity-Cervical Cancer-Womens (30-49 Years)-Screened / VIA

FROM
  -- TODO: replace with your actual source table(s)
  encounter e
  JOIN encounter_type et ON et.encounter_type_id = e.encounter_type

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
