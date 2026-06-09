# TabShare Auth Gateway

TabShare 专用认证网关，基于 `demo/v0.12` 的 OpenResty/JWKS/session POC 抽出。

## 职责

| 能力 | 说明 |
| --- | --- |
| 页面保护 | `/admin`、`/workbench/*`、`/viewer/*` 未登录跳登录页 |
| API 保护 | `/api/*` 未登录返回 JSON `401` |
| WebSocket 保护 | `/ws/*` 认证后转发 upgrade |
| 身份注入 | 清洗外部身份头后注入 `X-User-Id`、`X-Request-Id` |
| 静态资源 | `/static/*` 公开转发到 TabShare |

## 运行

从仓库根目录叠加 compose：

```sh
docker compose -f docker-compose.yml -f docker-compose.auth.yml up -d --build
```

访问网关：

```text
http://127.0.0.1:18080/admin
```

默认 POC 账号：

```text
admin@example.com / admin-password
```

## 边界

网关只认证身份，不判断 Workbench 权限。Workbench 授权由 TabShare 后端读取 `X-User-Id` 后自行判断。

当前主 `docker-compose.yml` 仍会把 TabShare 的 `8085` 直接暴露到宿主机。POC 验证时使用 `18080` 访问网关；生产化时需要移除或限制直连 `8085`。
