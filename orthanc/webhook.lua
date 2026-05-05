function OnStableStudy(studyId, tags, metadata)
    local url = os.getenv("ORTHANC_WEBHOOK_URL")
    if url == nil or url == "" then
        print("Webhook URL not configured. Skipping webhook.")
        return
    end

    print("Study " .. studyId .. " is stable. Triggering webhook to " .. url)
    
    -- Fetch the complete study JSON from the Orthanc REST API
    local studyInfo = RestApiGet('/studies/' .. studyId)
    
    -- Perform an HTTP POST to the configured webhook URL
    -- Set headers to indicate application/json to avoid Spring Boot parsing it as form parameters
    local headers = {
        ["Content-Type"] = "application/json"
    }
    
    local response = HttpPost(url, studyInfo, headers)
    
    print("Webhook triggered successfully.")
end
