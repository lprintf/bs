local cjson = require "cjson.safe"
local jwks = require "jwks"

local _M = {}

local ISSUER = os.getenv("AUTH_ISSUER") or "tabshare-auth"
local AUDIENCE = os.getenv("AUTH_AUDIENCE") or "tabshare-api"

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

local function split_jwt(token)
    local header, payload, signature = token:match("^([^.]+)%.([^.]+)%.([^.]+)$")
    if not header then
        return nil, "malformed_token"
    end
    return header, payload, signature
end

local function verify_eddsa(kid, signing_input, signature)
    local public_key, key_error = jwks.get_key(kid)
    if not public_key then
        return nil, key_error
    end

    local ok = public_key:verify(signature, signing_input)
    if not ok then
        return nil, "bad_signature"
    end

    return true, nil
end

function _M.verify_token(token)
    local encoded_header, encoded_payload, encoded_signature = split_jwt(token)
    if not encoded_header then
        return nil, encoded_payload
    end

    local header_json, header_decode_error = b64url_decode(encoded_header)
    if not header_json then
        return nil, header_decode_error
    end

    local payload_json, payload_decode_error = b64url_decode(encoded_payload)
    if not payload_json then
        return nil, payload_decode_error
    end

    local header = cjson.decode(header_json)
    if not header then
        return nil, "bad_header_json"
    end

    if header.alg ~= "EdDSA" then
        return nil, "bad_alg"
    end

    local signing_input = encoded_header .. "." .. encoded_payload
    local signature, signature_decode_error = b64url_decode(encoded_signature)
    if not signature then
        return nil, signature_decode_error
    end

    local ok, verify_error = verify_eddsa(header.kid, signing_input, signature)
    if not ok then
        return nil, verify_error
    end

    local claims = cjson.decode(payload_json)
    if not claims then
        return nil, "bad_payload_json"
    end

    if claims.iss ~= ISSUER then
        return nil, "bad_issuer"
    end

    if claims.aud ~= AUDIENCE then
        return nil, "bad_audience"
    end

    if not claims.sub or claims.sub == "" then
        return nil, "missing_subject"
    end

    if not claims.exp or tonumber(claims.exp) <= ngx.time() then
        return nil, "expired"
    end

    return claims, nil
end

function _M.verify_authorization_header()
    local authorization = ngx.var.http_authorization or ""
    local token = authorization:match("^Bearer%s+(.+)$")
    if not token then
        return false, "missing_bearer_token"
    end

    local claims, err = _M.verify_token(token)
    if not claims then
        ngx.log(ngx.WARN, "jwt rejected: ", err)
        return false, err
    end

    return true, claims
end

return _M
