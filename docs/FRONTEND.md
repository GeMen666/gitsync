# 前端文档

Vue 3 + Vite。页面只做展示与触发，数据来自后端，不在前端拼状态。

---

## 1. 目录

```
frontend/src/
├── api.js                 接口层：所有 HTTP 调用
├── models/pair.js         Pair / Branch 数据模型
├── components/
│   ├── LoginPanel.vue     登录
│   ├── RepoList.vue       左侧仓库列表
│   └── BranchTable.vue    分支状态表格
├── App.vue                状态编排 + 详情面板
├── style.css              全局基础样式
└── main.js
```

规则：

- 组件里不出现 `fetch`，统一走 `api.js`
- 组件不自己算状态，全部从 `Pair` 对象派生
- 写操作完成后统一 `refresh()`，表格以后端数据为准

---

## 2. 数据模型 `models/pair.js`

### Branch

| 字段 | 来源 | 说明 |
|---|---|---|
| `branch` | `branch` | 分支名 |
| `localSha` | `local_sha` | 空表示本地没有 |
| `remoteSha` | `remote_sha` | 空表示远程没有 |
| `status` | `status` | `synced/pushed/pulled/pending/error/skipped` |
| `detail` | `detail` | 说明文字 |
| `backupBranch` | `backup_branch` | 备份分支名 |
| `backupSide` | `backup_side` | `local` / `remote` |
| `updatedAt` | `updated_at` | 最后更新时间 |
| `busy` | 前端 | `'' / push / pull`，**每个分支独立** |
| `error` | 前端 | 该分支最近一次错误 |

派生属性：

| 属性 | 含义 |
|---|---|
| `hasLocal` | `localSha` 非空 |
| `hasRemote` | `remoteSha` 非空 |
| `showActions` | 是否需要显示推送/拉取（`synced`、`pending` 不显示） |
| `isBusy` | 该分支是否有操作在跑 |

### Pair

| 字段 | 来源 | 说明 |
|---|---|---|
| `id` | `id` | 后端配对 id |
| `name` | `name` | 名称，默认等于 `localRepo` |
| `localRepo` | `local_repo` | `owner/name` |
| `remoteUrl` | `remote_url` | 远程地址 |
| `remoteUsername` | `remote_username` | 远程账号 |
| `hasRemoteSecret` | `has_remote_secret` | 是否存过凭据 |
| `retentionDays` | `retention_days` | 备份保留天数 |
| `branches` | `branches` | `Branch[]` |
| `lastUpdatedRaw` | `last_updated` | 后端给的最后更新时间 |

运行状态（前端自己维护，**每个仓库独立**）：

| 字段 | 说明 |
|---|---|
| `busy` | `'' / compare / sync / cred / remove`（仓库级操作） |
| `error` | 该仓库最近一次操作的错误信息 |

派生属性与方法：

| 名称 | 说明 |
|---|---|
| `isBusy` | 仓库级操作是否在跑 |
| `hasBranchBusy` | 是否有分支操作在跑 |
| `isLocked` | `isBusy \|\| hasBranchBusy`，用于禁用仓库级按钮 |
| `remoteLabel` | 左侧列表用的地址（去掉 `https://`） |
| `lastUpdated` | 转成本地时区的时间字符串 |
| `begin(action)` | 开始仓库级操作：置 busy、清 error |
| `end()` | 结束操作：清 busy |
| `fail(message)` | 记录错误 |
| `update(data)` | 用后端数据刷新字段（不动运行状态） |

---

## 3. 组件

### LoginPanel

| 项 | 说明 |
|---|---|
| props | 无 |
| emits | `success`（登录成功） |
| 职责 | 收账号密码，调 `api.login`，展示错误 |

### RepoList

| 项 | 说明 |
|---|---|
| props | `repos: Repo[]`、`pairMap: Record<full_name, Pair>`、`selected: string` |
| emits | `select(full_name)` |
| 职责 | 左侧列表；已配对的显示远程地址，未配对的显示“未配置同步” |

### BranchTable

| 项 | 说明 |
|---|---|
| props | `pair: Pair` |
| emits | `push(branch)`、`pull(branch)` |
| 职责 | 表格：本地分支 / 远程分支 / 状态 / 备注 / 操作 |
| 数据 | 直接读 `pair.branches` |
| 禁用 | `pair.isBusy || item.isBusy`，只影响本行 |
| 转圈 | `item.busy === action`，只出现在被点的行 |

### App

| 职责 | 说明 |
|---|---|
| 状态 | `me、repos、pairs(Pair[])、selected、error、adding` |
| 登录 | `LoginPanel @success` → `loadMe()` |
| 刷新 | `refresh()` 拉取 repos 与 pairs，**按 id 复用已有 Pair 对象** |
| 操作 | `run(pair, action, fn)`：`begin` → 调用 → `refresh` → `end` |
| 详情 | 远程地址、凭据状态、比对/同步/取消配对、最后更新时间 |
| 配对 | 未配对时展示远程地址/账号/令牌表单 |

---

## 4. 数据流

### 登录

```
LoginPanel → api.login → 成功 emit success
App.loadMe → api.me → refresh()
refresh → api.repos + api.pairs → 生成 Pair[]
```

### 比对 / 同步

```
点击按钮 → App.run(pair, 'compare'|'sync', () => api.compare|sync(pair.id))
run: pair.begin() → 请求 → await refresh() → pair.end()
refresh 按 id 复用 Pair，运行状态不会丢
```

### 单分支推送 / 拉取

```
BranchTable emit push(item) → App.branchAction(pair, item, 'push')
→ item.begin('push') → api.push(pair.id, item.branch)
→ refresh()（按分支名复用 Branch）→ item.end()
```

分支状态挂在 `Branch` 上，只影响这一行的按钮；仓库级按钮在此期间禁用（`pair.isLocked`）。

### 错误

- 仓库相关错误存在 `pair.error`，显示在详情区
- 配对（还没有 Pair 对象）的错误存在 `App.error`

---

## 5. 接口层 `api.js`

| 方法 | 对应后端 |
|---|---|
| `login(username, password)` | `POST /api/login` |
| `logout()` | `POST /api/logout` |
| `me()` | `GET /api/me` |
| `repos()` | `GET /api/repos` |
| `pairs()` | `GET /api/pairs` |
| `addPair(body)` | `POST /api/pairs` |
| `updatePair(id, body)` | `PATCH /api/pairs/{id}` |
| `deletePair(id)` | `DELETE /api/pairs/{id}` |
| `compare(id)` | `POST /api/pairs/{id}/compare` |
| `sync(id)` | `POST /api/pairs/{id}/sync` |
| `push(id, branch)` | `POST /api/pairs/{id}/branches/{branch}/push` |
| `pull(id, branch)` | `POST /api/pairs/{id}/branches/{branch}/pull` |

约定：

- 统一带 `cache: 'no-store'`
- 非 2xx 抛 `Error(detail)`，调用方只负责显示
- 响应字段与 [DEV](DEV.md) 一致

---

## 6. 显示规则

| 场景 | 表现 |
|---|---|
| 仓库有操作在跑 | 该仓库按钮与详情区置灰，其他仓库不受影响 |
| 分支操作中 | 只有该行按钮置灰并转圈，其他行不受影响 |
| `synced` / `pending` | 不显示推送/拉取按钮 |
| 只有本地 / 只有远程 | 只显示对应方向的按钮 |
| 未配对 | 显示配对表单，左侧显示“未配置同步” |
| 没有状态记录 | 提示“还没有状态记录，点『比对』生成” |

---

*文档版本：v0.1 | 更新日期：2026-11-01*
