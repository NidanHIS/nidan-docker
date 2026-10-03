-- ==============================================================
-- Dataset : 01-2 Hospital Summary Dataset (NEW)
-- Group   : ORC Clinics/FCHV
-- Params  : :start_date  :end_date
-- ==============================================================
-- TODO: Replace NULL with actual SQL expressions

SELECT
  NULL AS health_facilities_within_catchment_area_outreach_planned,  -- Health Facilities within Catchment Area-Outreach Clinics-Planned
  NULL AS health_facilities_within_catchment_area_outreach_conducted,  -- Health Facilities within Catchment Area-Outreach Clinics-Conducted
  NULL AS health_facilities_within_catchment_area_outreach_served,  -- Health Facilities within Catchment Area-Outreach Clinics-People Served
  NULL AS health_facilities_within_catchment_area_planned,  -- Health Facilities within Catchment Area-Immunization Clinics-Planned
  NULL AS health_facilities_within_catchment_area_conducted,  -- Health Facilities within Catchment Area-Immunization Clinics-Conducted
  NULL AS people_served_from_immunization_clinic,  -- People Served from Immunization Clinic
  NULL AS health_facilities_within_catchment_area_planned_2,  -- Health Facilities within Catchment Area-Immunization Sessions-Planned
  NULL AS health_facilities_within_catchment_area_conducted_2,  -- Health Facilities within Catchment Area-Immunization Sessions-Conducted
  NULL AS immunization_hygiene_sessions_planned,  -- Immunization-Hygiene sessions planned
  NULL AS immunization_hygiene_sessions_conducted,  -- Immunization-Hygiene sessions conducted
  NULL AS immunization_people_benefitted_from_hygiene_session,  -- Immunization-People benefitted from hygiene session
  NULL AS total_fchvs_within_catchment_area,  -- Total FCHVs within Catchment Area
  NULL AS total_no_of_fchvs_report_submitted,  -- Total no.of FCHVs Report submitted
  NULL AS health_facilities_within_catchment_area_fchvs_served   -- Health Facilities within Catchment Area-FCHVs-People Served

FROM
  -- TODO: replace with your actual source table(s)
  encounter e
  JOIN encounter_type et ON et.encounter_type_id = e.encounter_type

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
