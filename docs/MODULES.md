# 后端模块与接口图

按层列出接口、实现和函数。设计取舍见 [ARCHITECTURE](ARCHITECTURE.md)，请求字段见 [DEV](DEV.md)。

---

## 1. 总览

```
                      ┌────────────────────────┐
                      │  前端 Vue (api.js)      │
                      └───────────┬────────────┘
                                  │ HTTP /api
┌─────────────────────────────────▼──────────────────────────────────┐
│ 接口层  api/app.py                                                  │
│  login / logout / me / repos / pairs / compare / sync / push / pull│
└───────┬──────────────────────────────────────────┬─────────────────┘
        │ RepoProvider（登录、仓库、权限）           │ SyncService
        ▼                                          ▼
┌───────────────────┐              ┌───────────────────────────────┐
│ adapters/gitea.py │              │ 处理层 services/sync.py        │
│ GiteaProvider     │              │ SyncService                   │
└───────────────────┘              └───┬───────────┬───────────┬───┘
                                       │           │           │
                          GitTransport │  StateStore│  SecretVault
                                       ▼           ▼           ▼
                          ┌──────────────┐ ┌────────────┐ ┌──────────┐
                          │ git 操作层    │ │ 数据库层    │ │ 凭据层     │
                          │ git_cli.py   │ │ store.py   │ │ vault.py │
                          └──────────────┘ └────────────┘ └──────────┘
```

依赖方向单向：`api → services → ports ← adapters`。

---

## 2. 接口（ports）

### GitTransport / GitWorkspace — `ports/git.py`

| 接口         | 方法          | 入参                              | 返回                           |
| ------------ | ------------- | --------------------------------- | ------------------------------ |
| GitTransport | `list_refs`   | `Endpoint`                        | `dict[branch, sha]`            |
| GitTransport | `workspace`   | —                                 | `GitWorkspace`（上下文管理器） |
| GitWorkspace | `fetch`       | `Endpoint, branch`                | `sha`                          |
| GitWorkspace | `commit_time` | `sha`                             | `int`（unix 秒）               |
| GitWorkspace | `push`        | `Endpoint, sha, branch`           | —（非快进抛错）                |
| GitWorkspace | `overwrite`   | `Endpoint, sha, branch, expected` | —                              |
| GitWorkspace | `delete`      | `Endpoint, branch`                | —                              |

异常：`GitError` / `NonFastForward` / `LeaseMismatch`。
数据：`Endpoint(name, url, username, token)`。

### StateStore — `ports/store.py`

| 方法             | 入参                                                                                                    | 返回                        |
| ---------------- | ------------------------------------------------------------------------------------------------------- | --------------------------- |
| `close`          | —                                                                                                       | —                           |
| `create_session` | `sid, username, token`                                                                                  | —                           |
| `get_session`    | `sid`                                                                                                   | `Session \| None`           |
| `delete_session` | `sid`                                                                                                   | —                           |
| `add_pair`       | `name, local_repo, remote_url, owner=, remote_username=, remote_secret=, push=, pull=, retention_days=` | `Pair`                      |
| `get_pair`       | `name`                                                                                                  | `Pair`（不存在抛 KeyError） |
| `get_pair_by_id` | `pair_id`                                                                                               | `Pair \| None`              |
| `list_pairs`     | `owner=None`                                                                                            | `list[Pair]`                |
| `update_pair`    | `name, remote_url=, remote_username=, remote_secret=, retention_days=`                                  | `Pair`                      |
| `delete_pair`    | `name`                                                                                                  | —                           |
| `save_branch`    | `pair_id, branch, local_sha=, remote_sha=, status=, backup_branch=, backup_side=, detail=`              | —                           |
| `get_branch`     | `pair_id, branch`                                                                                       | `BranchState \| None`       |
| `list_branches`  | `pair_id`                                                                                               | `list[BranchState]`         |
| `prune_branches` | `pair_id, keep`                                                                                         | —                           |

数据：`Session`、`Pair`、`BranchState`。

### RepoProvider — `ports/provider.py`

| 方法           | 入参                       | 返回               |
| -------------- | -------------------------- | ------------------ |
| `verify_login` | `username, password`       | `User`             |
| `issue_token`  | `username, password, name` | `str`              |
| `get_user`     | `token`                    | `User`             |
| `list_repos`   | `token`                    | `list[RemoteRepo]` |
| `get_repo`     | `token, full_name`         | `RemoteRepo`       |

异常：`ProviderError`。
数据：`User(login, full_name, avatar_url)`、`RemoteRepo(full_name, private, empty, mirror, push, pull)`。

### SecretVault — `ports/vault.py`

| 方法      | 入参     | 返回     |
| --------- | -------- | -------- |
| `encrypt` | `plain`  | `cipher` |
| `decrypt` | `cipher` | `plain`  |

### Settings — `ports/settings.py`

`path, local_url, local_username, local_token, remote_username, remote_token, db_path, temp_dir, log_dir`

---

## 3. 处理层 — `services/sync.py`

`SyncService(settings, store, vault, transport)`

| 方法        | 入参           | 返回            | 说明                     |
| ----------- | -------------- | --------------- | ------------------------ |
| `compare`   | `Pair`         | `list[Outcome]` | 只读比对，结果写回状态表 |
| `sync_pair` | `Pair`         | `list[Outcome]` | 执行同步                 |
| `push`      | `Pair, branch` | `Outcome`       | 单分支快进               |
| `pull`      | `Pair, branch` | `Outcome`       | 单分支快进               |

内部：

| 方法            | 作用                             |
| --------------- | -------------------------------- |
| `_endpoints`    | 组装本地/远程 Endpoint           |
| `_remote_token` | 取远程凭据（配对优先，回退配置） |
| `_run`          | 遍历分支，按 apply 决定是否写    |
| `_sync_branch`  | 单分支判定与搬运                 |
| `_persist`      | 把结果写回 branches              |
| `_cleanup`      | 清理过期备份                     |
| `_manual_move`  | 单分支快进实现                   |

数据：`Outcome(repo, branch, status, detail, backup_branch, backup_side, local_sha, remote_sha)`。

---

## 4. 适配器

### `adapters/git_cli.py`

| 类                | 方法                                                  |
| ----------------- | ----------------------------------------------------- |
| `CliGitTransport` | `list_refs`、`workspace`                              |
| `CliGitWorkspace` | `fetch`、`commit_time`、`push`、`overwrite`、`delete` |

### `adapters/store.py`

`Store` 实现 `StateStore` 全部方法，另含 `_migrate`（补列）。

### `adapters/gitea.py`

`GiteaProvider(base_url)` 实现 `RepoProvider`，内部 `_request` / `_send`。

### `adapters/vault.py`

`FernetVault(data_dir)` 实现 `SecretVault`。

### `adapters/config.py`

`load_config(path) -> Settings`，含权限校验（600）。

---

## 5. 接口层 — `api/app.py`

`create_app(config_path) -> FastAPI`

| 路由                                     | 方法   | 作用                     |
| ---------------------------------------- | ------ | ------------------------ |
| `/api/login`                             | POST   | 校验密码、换令牌、建会话 |
| `/api/logout`                            | POST   | 删会话                   |
| `/api/me`                                | GET    | 当前用户                 |
| `/api/repos`                             | GET    | 当前用户仓库             |
| `/api/pairs`                             | GET    | 配对列表（含 branches）  |
| `/api/pairs`                             | POST   | 新建配对                 |
| `/api/pairs/{id}`                        | PATCH  | 更新配对                 |
| `/api/pairs/{id}`                        | DELETE | 删除配对                 |
| `/api/pairs/{id}/compare`                | POST   | 只读比对                 |
| `/api/pairs/{id}/sync`                   | POST   | 执行同步                 |
| `/api/pairs/{id}/branches/{branch}/push` | POST   | 单分支推送               |
| `/api/pairs/{id}/branches/{branch}/pull` | POST   | 单分支拉取               |

内部辅助：`session_of`、`token_of`、`service_for`、`pair_view`、`owned_pair`、`require_push`。

---

## 6. CLI — `cli.py`

| 命令              | 函数              |
| ----------------- | ----------------- |
| `repos`           | `cmd_repos`       |
| `repo add`        | `cmd_repo_add`    |
| `repo list`       | `cmd_repo_list`   |
| `repo remove`     | `cmd_repo_remove` |
| `sync`            | `cmd_sync`        |
| `status [--live]` | `cmd_status`      |

装配：`_store()` 读配置 + 建 Store；`_service()` 组装 SyncService。

---

## 7. 调用时序

### 同步

```
api.sync(pair_id)
 └─ SyncService.sync_pair(pair)
     ├─ GitTransport.list_refs(local) / (remote)
     ├─ GitTransport.workspace()
     │   └─ 每个分支：
     │       ├─ fetch(local|remote)
     │       ├─ commit_time(...)  比新旧
     │       └─ push() 或 overwrite()
     ├─ StateStore.save_branch(...)
     └─ GitWorkspace.delete(...)  清理过期备份
```

### 单分支推送

```
api.branch_push(pair_id, branch)
 └─ SyncService.push(pair, branch)
     ├─ list_refs(local) / (remote)
     ├─ workspace().fetch(local, branch) → push(remote, sha, branch)
     └─ StateStore.save_branch(...)
```

### 登录

```
api.login(username, password)
 ├─ RepoProvider.verify_login  → User
 ├─ RepoProvider.issue_token   → token
 └─ StateStore.create_session  → cookie
```

---

*文档版本：v0.1 | 更新日期：2026-11-01*
