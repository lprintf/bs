FROM openresty/openresty:alpine

RUN apk add --no-cache ca-certificates curl perl python3 \
    && /usr/local/openresty/bin/opm get fffonion/lua-resty-openssl \
    && /usr/local/openresty/bin/opm get agentzh/lua-resty-http

COPY bin /usr/local/bin
COPY lua /etc/nginx/lua
COPY nginx.conf /usr/local/openresty/nginx/conf/nginx.conf
RUN chmod +x /usr/local/bin/gateway-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/gateway-entrypoint.sh"]
