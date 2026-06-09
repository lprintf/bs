local cjson = require "cjson.safe"
local http = require "resty.http"

local _M = {}

local SESSION_INTROSPECT_URL = os.getenv("SESSION_INTROSPECT_URL")
    or "http://auth-service:8000/internal/session/introspect"
local SESSION_RESOLVE_URL = os.getenv("SESSION_RESOLVE_URL")
    or "http://auth-service:8000/internal/session/resolve"
local HTTP_TIMEOUT_MS = 2000

local function cookie_value(name)
    local cookies = ngx.var.http_cookie or ""
    for item in cookies:gmatch("([^;]+)") do
        local key, value = item:match("^%s*([^=]+)=([^;]*)%s*$")
        if key == name then
            return value
        end
    end
    return nil
end

function _M.introspect_claims(claims)
    if not claims or not claims.sub or claims.sub == "" then
        return nil, "missing_subject"
    end

    if not claims.sid or claims.sid == "" then
        return nil, "missing_session"
    end

    local httpc = http.new()
    httpc:set_timeout(HTTP_TIMEOUT_MS)

    local res, request_error = httpc:request_uri(SESSION_INTROSPECT_URL, {
        method = "POST",
        headers = {
            ["X-Internal-Auth-Subject"] = claims.sub,
            ["X-Internal-Auth-Session"] = claims.sid,
            ["X-Request-Id"] = ngx.var.request_id or "",
        },
        body = "{}",
    })

    if not res then
        ngx.log(ngx.WARN, "session introspection failed: ", request_error)
        return nil, "introspection_unavailable"
    end

    if res.status ~= 200 then
        ngx.log(ngx.WARN, "session introspection rejected: status=", res.status)
        return nil, "introspection_rejected"
    end

    local body = cjson.decode(res.body or "")
    if not body or body.active ~= true then
        return nil, "inactive_session"
    end

    return true, body
end

function _M.resolve_cookie_session()
    local sid = cookie_value("demo_auth_sid")
    if not sid or sid == "" then
        return nil, "missing_session_cookie"
    end

    local httpc = http.new()
    httpc:set_timeout(HTTP_TIMEOUT_MS)

    local res, request_error = httpc:request_uri(SESSION_RESOLVE_URL, {
        method = "POST",
        headers = {
            ["X-Internal-Auth-Session"] = sid,
            ["X-Request-Id"] = ngx.var.request_id or "",
        },
        body = "{}",
    })

    if not res then
        ngx.log(ngx.WARN, "session resolve failed: ", request_error)
        return nil, "session_resolve_unavailable"
    end

    if res.status ~= 200 then
        ngx.log(ngx.WARN, "session resolve rejected: status=", res.status)
        return nil, "session_resolve_rejected"
    end

    local body = cjson.decode(res.body or "")
    if not body or body.active ~= true or not body.user_id or body.user_id == "" then
        return nil, "inactive_session"
    end

    return {
        sub = body.user_id,
        sid = sid,
    }, nil
end

return _M
