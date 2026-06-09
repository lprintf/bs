# TabShare Auth Service POC

认证服务 POC，来自 `demo/v0.12`，用于本地验证认证网关边界。

## 职责

| 能力 | 说明 |
| --- | --- |
| 登录页 | `/login/page` |
| 登录 API | `/login` |
| 全局退出 | `/logout` |
| JWKS | `/.well-known/jwks.json` |
| session introspection | `/internal/session/introspect` |
| cookie session resolve | `/internal/session/resolve` |

## 密钥

镜像构建时会生成 POC 用 Ed25519 key，不提交私钥。生产环境应接入外部密钥管理或挂载稳定密钥。

## 说明

后续钉钉登录应作为 Auth Service Provider 接入，再映射到内部 `user_id`。TabShare 只消费网关注入的内部 `X-User-Id`。
