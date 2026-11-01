# 模块、接口与数据结构

按「前端 → 后端接口层 → 后端处理层 → 数据库层 / Gitea API 调用层 / git 操作层」的顺序整理。
设计取舍见 [ARCHITECTURE](ARCHITECTURE.md)，表结构字段见 [DEV](DEV.md)，前端细节见 [FRONTEND](FRONTEND.md)。

---

## 0. 总览

```
┌──────────────────────────────┐
│ 前端（Vue）                   │
│ src/api.js  components/      │
└──────────────┬───────────────┘
               │ HTTP /api
┌──────────────▼───────────────┐
│ 后端接口层  api/app.py        │
│ 路由、会话、参数校验、权限     │
└──────────────┬───────────────┘
               │ 调用服务
┌──────────────▼───────────────┐
│ 后端处理层  services/sync.py  │
│ SyncService：比对、同步、选边  │
└───┬──────────────┬───────────┴───────────┬──────────────┐
    │              │                       │              │
┌───▼──────────┐ ┌─▼───────────────┐ ┌─────▼──────────┐ ┌─▼───────────┐
│ 数据库层      │ │ Gitea API 调用层 │ │ git 操作层      │ │ 凭据层       │
│ adapters/    │ │ adapters/       │ │ adapters/      │ │ adapters/   │
│ store.py     │ │ gitea.py        │ │ git_cli.py     │ │ vault.py    │
└──────────────┘ └─────────────────┘ └────────────────┘ └─────────────┘
        ▲                  ▲                  ▲                ▲
        └──────────────────┴──── ports/ ──────┴────────────────┘
                     （各层只依赖接口定义）
```

依赖方向：接口层和处理层只 import `ports/`，实现放在 `adapters/`，装配在入口（`api/app.py`、`cli.py`）。

---

## 1. 数据结构

### 1.1 后端

| 结构          | 字段                                                                                                                         | 说明                              |
| ------------- | ---------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| `Settings`    | `path, local_url, local_username, local_token, remote_username, remote_token, db_path, temp_dir, log_dir`                    | 运行时配置（`ports/settings.py`） |
| `Session`     | `id, username, token, created_at`                                                                                            | 登录会话，token 是 Gitea 令牌     |
| `Pair`        | `id, owner, name, local_repo, remote_url, remote_username, remote_secret, push_enabled, pull_enabled, backup_retention_days` | 一个仓库配对                      |
| `BranchState` | `pair_id, branch, local_sha, remote_sha, status, backup_branch, backup_side, backup_missing, updated_at`                     | 分支状态                          |
| `User`        | `login, full_name, avatar_url`                                                                                               | Gitea 用户                        |
| `RemoteRepo`  | `full_name, private, empty, mirror, push, pull`                                                                              | Gitea 仓库                        |
| `Endpoint`    | `name, url, username, token`                                                                                                 | git 远端（本地/远程）             |
| `Outcome`     | `repo, branch, status, detail, backup_branch, backup_side, backup_missing, local_sha, remote_sha`                            | 一次操作的结果                    |

**分支状态取值**

| 值          | 含义                                   |
| ----------- | -------------------------------------- |
| `need_pull` | 远程有更新，需要拉取                   |
| `need_push` | 本地有更新，需要推送                   |
| `synced`    | 两端一致                               |
| `pending`   | 有冲突，较新一方已应用，另一版本在备份 |
| `error`     | 操作失败，原因在 `detail`              |

`backup_side` 表示备份保存的是哪一侧的版本（`local` / `remote`）。
`backup_missing` 表示备份已被保留期清理，此时不能再选边。

### 1.2 前端

| 结构     | 字段                                                                                                                                     |
| -------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| `Pair`   | `id, name, owner, localRepo, remoteUrl, remoteUsername, hasRemoteSecret, retentionDays, branches: Branch[], lastUpdatedRaw, busy, error` |
| `Branch` | `branch, localSha, remoteSha, status, detail, backupBranch, backupSide, backupMissing, busy, error`                                      |

派生属性见 [FRONTEND](FRONTEND.md)。

---

## 2. 接口定义（ports/）

### 2.1 数据库层接口 `StateStore` — `ports/store.py`

| 方法                                                                                                                 | 入参          | 返回                          |
| -------------------------------------------------------------------------------------------------------------------- | ------------- | ----------------------------- |
| `close()`                                                                                                            | —             | —                             |
| `create_session(sid, username, token)`                                                                               | —             | —                             |
| `get_session(sid)`                                                                                                   | `str`         | `Session \| None`             |
| `delete_session(sid)`                                                                                                | `str`         | —                             |
| `add_pair(name, local_repo, remote_url, *, owner, remote_username, remote_secret, push, pull, retention_days)`       | —             | `Pair`                        |
| `get_pair(name)`                                                                                                     | `str`         | `Pair`（不存在抛 `KeyError`） |
| `get_pair_by_id(pair_id)`                                                                                            | `int`         | `Pair \| None`                |
| `list_pairs(owner=None)`                                                                                             | `str \| None` | `list[Pair]`                  |
| `update_pair(name, *, remote_url, remote_username, remote_secret, retention_days)`                                   | —             | `Pair`                        |
| `delete_pair(name)`                                                                                                  | `str`         | —                             |
| `save_branch(pair_id, branch, *, local_sha, remote_sha, status, backup_branch, backup_side, backup_missing, detail)` | —             | —                             |
| `get_branch(pair_id, branch)`                                                                                        | —             | `BranchState \| None`         |
| `list_branches(pair_id)`                                                                                             | `int`         | `list[BranchState]`           |
| `prune_branches(pair_id, keep)`                                                                                      | `set[str]`    | —                             |
| `mark_backup_missing(pair_id, branch, detail)`                                                                       | —             | —                             |

### 2.2 Gitea API 调用层接口 `RepoProvider` — `ports/provider.py`

| 方法                                    | 入参  | 返回               |
| --------------------------------------- | ----- | ------------------ |
| `verify_login(username, password)`      | —     | `User`             |
| `issue_token(username, password, name)` | —     | `str`              |
| `get_user(token)`                       | `str` | `User`             |
| `list_repos(token)`                     | `str` | `list[RemoteRepo]` |
| `get_repo(token, full_name)`            | —     | `RemoteRepo`       |

异常：`ProviderError(message, status)`。

### 2.3 git 操作层接口 `GitTransport` / `GitWorkspace` — `ports/git.py`

| 接口           | 方法                                      | 入参       | 返回                                |
| -------------- | ----------------------------------------- | ---------- | ----------------------------------- |
| `GitTransport` | `list_refs(ep)`                           | `Endpoint` | `dict[branch, sha]`                 |
| `GitTransport` | `workspace()`                             | —          | `GitWorkspace`（上下文管理器）      |
| `GitWorkspace` | `fetch(ep, branch)`                       | —          | `sha`                               |
| `GitWorkspace` | `commit_time(sha)`                        | `str`      | `int`（unix 秒）                    |
| `GitWorkspace` | `push(ep, sha, branch)`                   | —          | —（非快进抛 `NonFastForward`）      |
| `GitWorkspace` | `overwrite(ep, sha, branch, *, expected)` | —          | —（lease 不匹配抛 `LeaseMismatch`） |
| `GitWorkspace` | `delete(ep, branch)`                      | —          | —                                   |

异常：`GitError` / `NonFastForward` / `LeaseMismatch`。

### 2.4 凭据层接口 `SecretVault` — `ports/vault.py`

| 方法              | 入参  | 返回  |
| ----------------- | ----- | ----- |
| `encrypt(plain)`  | `str` | `str` |
| `decrypt(cipher)` | `str` | `str` |

---

## 3. 后端接口层 — `api/app.py`

`create_app(config_path) -> FastAPI`

| 路由                                        | 方法   | 作用                                       |
| ------------------------------------------- | ------ | ------------------------------------------ |
| `/api/login`                                | POST   | 校验账号密码、换令牌、建会话               |
| `/api/logout`                               | POST   | 删除会话                                   |
| `/api/me`                                   | GET    | 当前用户                                   |
| `/api/repos`                                | GET    | 当前用户可见仓库                           |
| `/api/pairs`                                | GET    | 配对列表（含 branches）                    |
| `/api/pairs`                                | POST   | 新建配对                                   |
| `/api/pairs/{id}`                           | PATCH  | 更新配对                                   |
| `/api/pairs/{id}`                           | DELETE | 删除配对                                   |
| `/api/pairs/{id}/compare`                   | POST   | 只读比对，写回状态                         |
| `/api/pairs/{id}/sync`                      | POST   | 执行同步                                   |
| `/api/pairs/{id}/branches/{branch}/push`    | POST   | 单分支快进推送                             |
| `/api/pairs/{id}/branches/{branch}/pull`    | POST   | 单分支快进拉取                             |
| `/api/pairs/{id}/branches/{branch}/resolve` | POST   | 冲突选边（`{"prefer":"local"\|"remote"}`） |

内部辅助：

| 函数                              | 作用                         |
| --------------------------------- | ---------------------------- |
| `session_of(request)`             | 从 cookie 取会话             |
| `token_of(request)`               | 取会话令牌                   |
| `service_for(session)`            | 用会话令牌装配 `SyncService` |
| `pair_view(pair)`                 | 对外表示，隐藏密文           |
| `owned_pair(request, pair_id)`    | 取配对并校验归属             |
| `require_push(token, local_repo)` | 校验写权限                   |

---

## 4. 后端处理层 — `services/sync.py`

`SyncService(settings, store, vault, transport)`

| 方法                            | 入参                    | 返回            | 说明                     |
| ------------------------------- | ----------------------- | --------------- | ------------------------ |
| `compare(pair)`                 | `Pair`                  | `list[Outcome]` | 只读比对，结果写回状态表 |
| `sync_pair(pair)`               | `Pair`                  | `list[Outcome]` | 执行同步                 |
| `push(pair, branch)`            | —                       | `Outcome`       | 单分支快进推送           |
| `pull(pair, branch)`            | —                       | `Outcome`       | 单分支快进拉取           |
| `resolve(pair, branch, prefer)` | `prefer: local\|remote` | `Outcome`       | 冲突选边，备份保留       |

内部：

| 方法                                    | 作用                                |
| --------------------------------------- | ----------------------------------- |
| `_endpoints(pair)`                      | 组装本地/远程 `Endpoint`            |
| `_remote_token(pair)`                   | 取远程凭据（配对优先，回退配置）    |
| `_run(pair, apply)`                     | 遍历分支，`apply` 决定是否落地      |
| `_sync_branch(...)`                     | 单分支判定与搬运                    |
| `_persist(...)`                         | 把结果写回 `branch_status`          |
| `_cleanup(...)`                         | 清理过期备份并标记 `backup_missing` |
| `_manual_move(pair, branch, direction)` | 单分支快进实现                      |

判定规则：

| 本地 | 远程 | 结果                                |
| ---- | ---- | ----------------------------------- |
| 有   | 无   | 创建到远程；比对时 `need_push`      |
| 无   | 有   | 创建到本地；比对时 `need_pull`      |
| 相同 | 相同 | `synced`                            |
| 不同 | 不同 | 较新者胜，另一版本存备份，`pending` |

---

## 5. 数据库层 — `adapters/store.py`

`Store(path)` 实现 `StateStore`，SQLite，连接跨线程复用、访问串行化。

| 内容     | 说明                                                      |
| -------- | --------------------------------------------------------- |
| 表       | `repos`、`sessions`、`branch_status`                      |
| 迁移     | `_migrate()` 按 `PRAGMA table_info` 补列                  |
| 映射     | `_pair` / `_branch` / `_session` 转成数据类               |
| 备份标记 | `mark_backup_missing` 清空可切换状态并把状态改为 `synced` |

字段定义见 [DEV](DEV.md) 第 2 节。

---

## 6. Gitea API 调用层 — `adapters/gitea.py`

`GiteaProvider(base_url)` 实现 `RepoProvider`。

| 方法           | 说明                               |
| -------------- | ---------------------------------- |
| `verify_login` | Basic 认证 `GET /api/v1/user`      |
| `issue_token`  | `POST /api/v1/users/{u}/tokens`    |
| `get_user`     | `GET /api/v1/user`（token）        |
| `list_repos`   | `GET /api/v1/user/repos`，自动翻页 |
| `get_repo`     | `GET /api/v1/repos/{owner}/{repo}` |

内部：`_request` 拼 URL 与鉴权头，`_send` 发请求并把 HTTP 错误转成 `ProviderError`。

---

## 7. git 操作层 — `adapters/git_cli.py`

| 类                | 方法          | 说明                                |
| ----------------- | ------------- | ----------------------------------- |
| `CliGitTransport` | `list_refs`   | `git ls-remote --heads`             |
| `CliGitTransport` | `workspace`   | 生成临时裸仓库，退出时删除          |
| `CliGitWorkspace` | `fetch`       | `git fetch` 到 `refs/sync/<branch>` |
| `CliGitWorkspace` | `commit_time` | `git log -1 --format=%ct`           |
| `CliGitWorkspace` | `push`        | 快进推送                            |
| `CliGitWorkspace` | `overwrite`   | `--force-with-lease`                |
| `CliGitWorkspace` | `delete`      | 删除分支（仅清理备份）              |

凭据通过 `GIT_ASKPASS` 脚本 + 环境变量传入，不落盘、不进命令行。

---

## 8. 调用时序

### 同步

```
api.sync(pair_id)
 └─ SyncService.sync_pair(pair)
     ├─ GitTransport.list_refs(local) / (remote)
     ├─ GitTransport.workspace()
     │   └─ 每个分支：fetch → commit_time 比较 → push / overwrite
     ├─ StateStore.save_branch(...)
     └─ GitWorkspace.delete(...) 清理过期备份
```

### 比对

```
api.compare(pair_id)
 └─ SyncService.compare(pair)
     ├─ 同上，但不写仓库（dry run）
     └─ StateStore.save_branch(...) + prune_branches(...)
```

### 冲突选边

```
api.branch_resolve(pair_id, branch, prefer)
 └─ SyncService.resolve(pair, branch, prefer)
     ├─ 取备份版本 / 当前版本
     ├─ 被替换的版本存为新备份
     └─ 两端对齐到目标版本，状态写 synced
```

### 登录

```
api.login(username, password)
 ├─ RepoProvider.verify_login  → User
 ├─ RepoProvider.issue_token   → token
 └─ StateStore.create_session  → 下发 cookie
```

---

## 9. 命令行

| 命令              | 函数              | 说明            |
| ----------------- | ----------------- | --------------- |
| `repos`           | `cmd_repos`       | 列出 Gitea 仓库 |
| `repo add`        | `cmd_repo_add`    | 登记配对        |
| `repo list`       | `cmd_repo_list`   | 列出配对        |
| `repo remove`     | `cmd_repo_remove` | 删除配对        |
| `sync`            | `cmd_sync`        | 执行同步        |
| `status [--live]` | `cmd_status`      | 查看状态        |

装配：`_service()` 创建 `SyncService`，与接口层用同一套依赖。

---

*文档版本：v0.2 | 更新日期：2026-11-01*
