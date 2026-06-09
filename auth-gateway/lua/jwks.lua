local cjson = require "cjson.safe"
local http = require "resty.http"
local pkey = require "resty.openssl.pkey"

local _M = {}

local JWKS_URL = os.getenv("JWKS_URL") or "http://auth-service:8000/.well-known/jwks.json"
local JWKS_PATH = os.getenv("JWKS_PATH") or "/etc/nginx/jwks/jwks.json"
local REFRESH_INTERVAL = tonumber(os.getenv("JWKS_REFRESH_INTERVAL_SECONDS") or "30")
local FORCE_REFRESH_COOLDOWN = tonumber(os.getenv("JWKS_FORCE_REFRESH_COOLDOWN_SECONDS") or "2")
local HTTP_TIMEOUT_MS = 2000

local shared = ngx.shared.jwks_cache
local local_generation = nil
local local_keys = {}

local function read_file(path)
    local file, err = io.open(path, "rb")
    if not file then
        return nil, err
    end
    local value = file:read("*a")
    file:close()
    return value, nil
end

local function b64url_decode(value)
    local normalized = value:gsub("-", "+"):gsub("_", "/")
    local padding = #normalized % 4
    if padding == 2 then
        normalized = normalized .. "=="
    elseif padding == 3 then
        normalized = normalized .. "="
    elseif padding ~= 0 then
        return nil, "bad_base64url"
    end

    return ngx.decode_base64(normalized)
end

local function pem_wrap(label, der)
    local encoded = ngx.encode_base64(der)
    local lines = {}
    for i = 1, #encoded, 64 do
        lines[#lines + 1] = encoded:sub(i, i + 63)
    end
    return "-----BEGIN " .. label .. "-----\n"
        .. table.concat(lines, "\n")
        .. "\n-----END " .. label .. "-----\n"
end

local function ed25519_jwk_to_pem(jwk)
    local public_key, decode_error = b64url_decode(jwk.x or "")
    if not public_key then
        return nil, decode_error
    end
    if #public_key ~= 32 then
        return nil, "bad_ed25519_public_key_length"
    end

    local subject_public_key_info = string.char(
        0x30, 0x2a, 0x30, 0x05, 0x06, 0x03, 0x2b, 0x65,
        0x70, 0x03, 0x21, 0x00
    ) .. public_key
    return pem_wrap("PUBLIC KEY", subject_public_key_info)
end

local function jwk_to_pkey(jwk)
    if jwk.kty ~= "OKP" or jwk.crv ~= "Ed25519" or jwk.alg ~= "EdDSA" then
        return nil, "unsupported_jwk"
    end

    local pem, pem_error = ed25519_jwk_to_pem(jwk)
    if not pem then
        return nil, pem_error
    end

    local key, key_error = pkey.new(pem)
    if not key then
        return nil, "load_public_key_failed:" .. key_error
    end

    return key, nil
end

local function build_key_map(jwks_json)
    local jwks, decode_error = cjson.decode(jwks_json)
    if not jwks then
        return nil, "bad_jwks_json:" .. (decode_error or "unknown")
    end
    if type(jwks.keys) ~= "table" then
        return nil, "missing_jwks_keys"
    end

    local keys = {}
    local count = 0
    for _, jwk in ipairs(jwks.keys) do
        if type(jwk) == "table" and jwk.kid and jwk.kid ~= "" then
            local key, key_error = jwk_to_pkey(jwk)
            if key then
                keys[jwk.kid] = key
                count = count + 1
            else
                ngx.log(ngx.WARN, "ignored jwk kid=", jwk.kid, ": ", key_error)
            end
        end
    end

    if count == 0 then
        return nil, "no_supported_jwks_keys"
    end

    return keys, nil
end

local function publish_jwks(jwks_json, source)
    local keys, build_error = build_key_map(jwks_json)
    if not keys then
        return nil, build_error
    end

    local generation = tostring(ngx.now())
    shared:set("jwks_json", jwks_json)
    shared:set("generation", generation)
    shared:set("last_success_at", ngx.now())
    shared:set("last_success_source", source)

    local_keys = keys
    local_generation = generation
    return true, nil
end

local function load_local_from_shared()
    local generation = shared:get("generation")
    if not generation then
        return nil, "jwks_cache_empty"
    end

    if local_generation == generation then
        return true, nil
    end

    local jwks_json = shared:get("jwks_json")
    if not jwks_json then
        return nil, "jwks_cache_empty"
    end

    local keys, build_error = build_key_map(jwks_json)
    if not keys then
        return nil, build_error
    end

    local_keys = keys
    local_generation = generation
    return true, nil
end

function _M.refresh(reason)
    if not shared:add("refresh_lock", ngx.worker.pid(), 5) then
        return nil, "refresh_in_progress"
    end

    local httpc = http.new()
    httpc:set_timeout(HTTP_TIMEOUT_MS)
    local res, request_error = httpc:request_uri(JWKS_URL, { method = "GET" })
    shared:delete("refresh_lock")

    if not res then
        shared:set("last_error", request_error or "request_failed")
        ngx.log(ngx.WARN, "jwks refresh failed: ", request_error)
        return nil, request_error or "request_failed"
    end

    if res.status ~= 200 then
        local err = "bad_jwks_status:" .. tostring(res.status)
        shared:set("last_error", err)
        ngx.log(ngx.WARN, "jwks refresh failed: ", err)
        return nil, err
    end

    local ok, publish_error = publish_jwks(res.body, reason or "refresh")
    if not ok then
        shared:set("last_error", publish_error)
        ngx.log(ngx.WARN, "jwks refresh rejected: ", publish_error)
        return nil, publish_error
    end

    shared:delete("last_error")
    return true, nil
end

local function refresh_timer(premature)
    if premature then
        return
    end

    _M.refresh("periodic")
    local ok, timer_error = ngx.timer.at(REFRESH_INTERVAL, refresh_timer)
    if not ok then
        ngx.log(ngx.ERR, "failed to schedule jwks refresh timer: ", timer_error)
    end
end

local function force_refresh_for_unknown_kid()
    local now = ngx.now()
    local last_forced = shared:get("last_forced_refresh_at")
    if last_forced and now - last_forced < FORCE_REFRESH_COOLDOWN then
        return nil, "force_refresh_cooldown"
    end

    shared:set("last_forced_refresh_at", now)
    return _M.refresh("unknown_kid")
end

function _M.init_worker()
    if not shared:get("generation") then
        local jwks_json, read_error = read_file(JWKS_PATH)
        if jwks_json then
            local ok, publish_error = publish_jwks(jwks_json, "startup_file")
            if not ok then
                ngx.log(ngx.WARN, "startup jwks file rejected: ", publish_error)
            end
        else
            ngx.log(ngx.WARN, "startup jwks file missing: ", read_error)
        end
    end

    if ngx.worker.id() == 0 then
        local ok, timer_error = ngx.timer.at(REFRESH_INTERVAL, refresh_timer)
        if not ok then
            ngx.log(ngx.ERR, "failed to schedule jwks refresh timer: ", timer_error)
        end
    end
end

function _M.get_key(kid)
    if not kid or kid == "" then
        return nil, "missing_kid"
    end
    if kid:find("[^%w%-_%.]") then
        return nil, "bad_kid"
    end

    local ok, load_error = load_local_from_shared()
    if not ok then
        return nil, load_error
    end

    local key = local_keys[kid]
    if key then
        return key, nil
    end

    local refreshed = force_refresh_for_unknown_kid()
    if refreshed then
        load_local_from_shared()
        key = local_keys[kid]
        if key then
            return key, nil
        end
    end

    return nil, "unknown_kid"
end

return _M
