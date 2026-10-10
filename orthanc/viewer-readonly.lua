-- Orthanc's own read-only layer (backlog KC-30-02, KC-30-05). The gateway sends the viewer account on the image
-- viewer routes and the admin account only on the Orthanc UI (orthanc-admin); this holds whoever reaches Orthanc.
--   * the viewer account (ORTHANC_VIEWER_USERNAME, default nidan-viewer) only reads: GET / HEAD
--   * nobody runs scripts over REST (/tools/execute-script), whatever the account
-- Orthanc allows one IncomingHttpRequestFilter; keep it in this file.
local VIEWER = os.getenv('ORTHANC_VIEWER_USERNAME') or 'nidan-viewer'

function IncomingHttpRequestFilter(method, uri, ip, username, httpHeaders)
  if string.find(string.lower(uri), '/tools/execute%-script', 1) then
    return false
  end
  if username == VIEWER and method ~= 'GET' and method ~= 'HEAD' then
    return false
  end
  return true
end
