# Admin UI

`tabshare/admin-ui/` 是 TabShare 管理后台的 React/Vite 源码。它不在 `tabshare/Dockerfile` 中构建，必须在 Node 环境中单独构建后，由 FastAPI 静态托管。

## 职责

| 能力 | 说明 |
| --- | --- |
| Browser 管理 | 创建、启动、停止、删除持久浏览器 |
| Tab 管理 | 查看、关闭、选择可加入工作台的标签页 |
| Workbench 管理 | 创建工作台、添加/释放标签页、打开 viewer |
| 录制控制 | 调用后端 Workbench recording API |
| 编码配置 | 调用 `/api/codec` 切换 H.264、VP8、VP9、AV1 |

## 构建

```sh
npm ci
npm run build
```

`vite.config.ts` 固定输出目录：

```text
../server/static/admin-spa
```

生成后的文件由 `server.app` 在 `/admin` 路由优先托管。

## 开发

```sh
npm run dev
```

开发服务器只服务前端源码。需要完整后端 API、WebSocket 和静态资源路径时，使用根 compose 启动后端。

## 提交规则

前端源码和构建产物需要一起提交：

| 文件 | 原因 |
| --- | --- |
| `src/**` | 管理后台源码 |
| `../server/static/admin-spa/**` | 生产环境 `/admin` 实际托管文件 |

如果只改源码不提交构建产物，容器启动后的 `/admin` 仍会加载旧页面。
