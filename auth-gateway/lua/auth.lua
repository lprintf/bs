local cjson = require "cjson.safe"
local jwt = require "jwt"
local session = require "session"

local _M = {}

local function write_json(status, body)
    ngx.status = status
    ngx.header.content_type = "application/json"
    ngx.say(cjson.encode(body))
    return ngx.exit(status)
end

local function redirect_to_login()
    local proto = ngx.var.http_x_forwarded_proto
    if not proto or proto == "" then
        proto = ngx.var.scheme or "http"
    end
    local host = ngx.var.http_host or ngx.var.host
    local redirect_uri = proto .. "://" .. host .. ngx.var.request_uri
    return ngx.redirect("/login/page?redirect_uri=" .. ngx.escape_uri(redirect_uri), ngx.HTTP_MOVED_TEMPORARILY)
end

local function authenticate()
    local authorization = ngx.var.http_authorization or ""
    if authorization:match("^Bearer%s+.+$") then
        local ok, claims_or_error = jwt.verify_authorization_header()
        if ok then
            return true, claims_or_error
        end
        return false, claims_or_error
    end

    local claims, cookie_error = session.resolve_cookie_session()
    if claims then
        return true, claims
    end

    return false, cookie_error or "missing_credentials"
end

local function maybe_require_session_check(claims)
    local require_session_check = ngx.var.http_x_require_session_check or ""
    if require_session_check:lower() ~= "true" and require_session_check ~= "1" then
        return true, nil
    end

    local session_ok, session_error = session.introspect_claims(claims)
    if not session_ok then
        return false, session_error
    end

    ngx.var.session_verified = "true"
    ngx.var.session_verified_at = tostring(ngx.time())
    return true, nil
end

function _M.require_json()
    local ok, claims_or_error = authenticate()
    if not ok then
        return write_json(ngx.HTTP_UNAUTHORIZED, {
            error = "invalid_token",
            reason = claims_or_error,
        })
    end

    local session_ok, session_error = maybe_require_session_check(claims_or_error)
    if not session_ok then
        return write_json(ngx.HTTP_UNAUTHORIZED, {
            error = "inactive_session",
            reason = session_error,
        })
    end

    ngx.var.auth_user = claims_or_error.sub
end

function _M.require_page()
    local ok, claims_or_error = authenticate()
    if not ok then
        return redirect_to_login()
    end

    local session_ok, session_error = maybe_require_session_check(claims_or_error)
    if not session_ok then
        ngx.log(ngx.WARN, "page session check failed: ", session_error)
        return redirect_to_login()
    end

    ngx.var.auth_user = claims_or_error.sub
end

return _M
