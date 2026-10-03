-- ==============================================================
-- Dataset : 01-2 Hospital Summary Dataset (NEW)
-- Group   : Hospital Services
-- Params  : :start_date  :end_date
-- ==============================================================
-- TODO: Replace NULL with actual SQL expressions

SELECT
  NULL AS new_clients_served_0_9_f,  -- New Clients Served / 0 to 9 Yrs, Female
  NULL AS new_clients_served_0_9_m,  -- New Clients Served / 0 to 9 Yrs, Male
  NULL AS new_clients_served_10_14_f,  -- New Clients Served / 10 to 14 Yrs, Female
  NULL AS new_clients_served_10_14_m,  -- New Clients Served / 10 to 14 Yrs, Male
  NULL AS new_clients_served_15_19_f,  -- New Clients Served / 15 to 19 Yrs, Female
  NULL AS new_clients_served_15_19_m,  -- New Clients Served / 15 to 19 Yrs, Male
  NULL AS new_clients_served_20_59_f,  -- New Clients Served / 20 to 59 Yrs, Female
  NULL AS new_clients_served_20_59_m,  -- New Clients Served / 20 to 59 Yrs, Male
  NULL AS new_clients_served_60_69_f,  -- New Clients Served / 60 to 69 Years, Female
  NULL AS new_clients_served_60_69_m,  -- New Clients Served / 60 to 69 Years, Male
  NULL AS new_clients_served_70_plus_f,  -- New Clients Served / 70 and above, Female
  NULL AS new_clients_served_70_plus_m,  -- New Clients Served / 70 and above, Male
  NULL AS total_clients_served_0_9_f,  -- Total Clients Served / 0 to 9 Yrs, Female
  NULL AS total_clients_served_0_9_m,  -- Total Clients Served / 0 to 9 Yrs, Male
  NULL AS total_clients_served_10_14_f,  -- Total Clients Served / 10 to 14 Yrs, Female
  NULL AS total_clients_served_10_14_m,  -- Total Clients Served / 10 to 14 Yrs, Male
  NULL AS total_clients_served_15_19_f,  -- Total Clients Served / 15 to 19 Yrs, Female
  NULL AS total_clients_served_15_19_m,  -- Total Clients Served / 15 to 19 Yrs, Male
  NULL AS total_clients_served_20_59_f,  -- Total Clients Served / 20 to 59 Yrs, Female
  NULL AS total_clients_served_20_59_m,  -- Total Clients Served / 20 to 59 Yrs, Male
  NULL AS total_clients_served_60_69_f,  -- Total Clients Served / 60 to 69 Years, Female
  NULL AS total_clients_served_60_69_m,  -- Total Clients Served / 60 to 69 Years, Male
  NULL AS total_clients_served_70_plus_f,  -- Total Clients Served / 70 and above, Female
  NULL AS total_clients_served_70_plus_m   -- Total Clients Served / 70 and above, Male

FROM
  -- TODO: replace with your actual source table(s)
  encounter e
  JOIN encounter_type et ON et.encounter_type_id = e.encounter_type

WHERE
  e.encounter_datetime BETWEEN :start_date AND :end_date
  AND e.voided = 0
