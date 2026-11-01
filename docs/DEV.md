# gitsync 开发文档

接口、字段与错误码的详细定义。架构与取舍见根目录 [README](../README.md)。

**参考版本：Gitea 28.2.0**

---

## 1. 通用约定

| 项       | 约定                                                       |
| -------- | ---------------------------------------------------------- |
| 后端前缀 | `/api`（本项目自己的接口）                                 |
| Gitea    | `{gitea_url}/api/v1/...`                                   |
| 认证     | cookie `gitsync_session`（httponly），值为随机 session id  |
| 时间     | 存储与传输统一 UTC ISO8601，前端转本地时区                 |
| 编码     | UTF-8，JSON；branch 名可能含 `/`，路径参数需按 URL 规则编码 |
| 分页     | 列表接口默认全量，后续需要再加 `page/limit`                |

### 错误响应

```json
{ "detail": "人类可读的说明" }
```

| 状态码 | 含义                         |
| ------ | ---------------------------- |
| 400    | 参数错误、git 操作失败        |
| 401    | 未登录、令牌失效              |
| 403    | 无权限（非所有者且非 admin）  |
| 404    | 仓库/配对/分支不存在          |
| 409    | 冲突（并发修改）              |
| 502    | 上游（Gitea/远程 git）异常    |

---

## 2. 数据模型

### sessions

| 字段       | 类型 | 说明                                   |
| ---------- | ---- | -------------------------------------- |
| id         | TEXT | 主键，随机 32 字节，放 cookie          |
| username   | TEXT | Gitea 登录名                           |
| token_enc  | TEXT | 登录时换来的令牌（加密）               |
| created_at | TEXT | UTC ISO8601                            |

### repos

| 字段               | 类型    | 说明                                   |
| ------------------ | ------- | -------------------------------------- |
| id                 | INTEGER | 主键                                   |
| name               | TEXT    | 唯一，默认等于 local_repo              |
| owner              | TEXT    | Gitea 登录名，用于权限判断             |
| local_repo         | TEXT    | `owner/name`                           |
| remote_url         | TEXT    | 远程 git 地址                          |
| remote_username    | TEXT    | 远程账号，可空                         |
| remote_secret_enc  | TEXT    | 远程令牌/密码（Fernet 密文），可空     |
| retention_days     | INTEGER | 备份保留天数，默认 7                   |
| created_at         | TEXT    | UTC ISO8601                            |

### branches

| 字段          | 类型    | 说明                                                   |
| ------------- | ------- | ------------------------------------------------------ |
| pair_id       | INTEGER | 外键 -> repos.id                                       |
| branch        | TEXT    | 分支名                                                 |
| local_sha     | TEXT    | 本地端该分支的提交，空表示不存在                       |
| remote_sha    | TEXT    | 远程端该分支的提交，空表示不存在                       |
| status        | TEXT    | 见 §3                                                  |
| detail        | TEXT    | 说明文字                                               |
| backup_branch | TEXT    | 备份分支名，可空                                       |
| backup_side   | TEXT    | 备份在哪一侧：`local` / `remote`                       |
| updated_at    | TEXT    | UTC ISO8601                                            |

主键 `(pair_id, branch)`。

---

## 3. 状态值

分支状态只有五种：

| 值 | 含义 |
| --- | --- |
| `need_pull` | 远程有更新，需要拉取 |
| `need_push` | 本地有更新，需要推送 |
| `synced` | 两端一致 |
| `pending` | 有冲突，已按较新者胜处理并备份 |
| `error` | 操作失败，详情见 detail |

比对（compare）返回前四种；同步（sync）成功后状态为 `synced` 或 `pending`。

---

## 4. 后端接口

### 4.1 认证

#### `POST /api/login`

```json
// 请求
{ "username": "alice", "password": "***" }

// 200
{ "login": "alice", "full_name": "..." }
```

失败：`401`（账号或密码错误）、`502`（Gitea 不可达）。

#### `POST /api/logout`

```json
{ "ok": true }
```

#### `GET /api/me`

```json
{ "login": "alice", "full_name": "...", "avatar_url": "..." }
```

### 4.2 仓库（来自 Gitea）

#### `GET /api/repos`

```json
[
  {
    "full_name": "alice/project",
    "private": true,
    "empty": false,
    "mirror": false,
    "push": true,
    "pull": true
  }
]
```

### 4.3 配对

#### `GET /api/pairs`

```json
[
  {
    "id": 1,
    "name": "alice/project",
    "owner": "alice",
    "local_repo": "alice/project",
    "remote_url": "https://github.com/you/project.git",
    "remote_username": "you",
    "has_remote_secret": true,
    "last_updated": "2026-11-01T08:06:03+00:00",
    "branches": [
      {
        "branch": "main",
        "local_sha": "abc123...",
        "remote_sha": "abc123...",
        "status": "synced",
        "detail": "",
        "backup_branch": "main.bak.20261101080603",
        "backup_side": "remote",
        "updated_at": "2026-11-01T08:06:03+00:00"
      }
    ]
  }
]
```

> `last_updated` 取该配对下所有分支 `updated_at` 的最大值。

#### `POST /api/pairs`

```json
// 请求
{
  "local_repo": "alice/project",
  "remote_url": "https://github.com/you/project.git",
  "remote_username": "you",          // 可选
  "remote_token": "ghp_***"          // 可选，私有仓库必填
}
// 200：返回 Pair 对象
```

校验：仓库必须存在且当前用户有写权限（规则见 §6）。

#### `PATCH /api/pairs/{id}`

```json
{
  "remote_url": "...",       // 可选
  "remote_username": "...",  // 可选
  "remote_token": "...",     // 可选，非空时覆盖
  "retention_days": 7        // 可选，备份保留天数
}
```

#### `DELETE /api/pairs/{id}`

```json
{ "ok": true }
```

只删除配对与状态记录，**不动任何仓库**。

### 4.4 同步

#### `POST /api/pairs/{id}/compare`

只读比对：拉取两端分支列表与提交时间，把结果写回 `branches`，不改动仓库。

```json
{ "branches": [ /* 同 §4.3 的 branches 元素 */ ] }
```

#### `POST /api/pairs/{id}/sync`

按固定规则（较新者胜）执行，返回执行后的分支状态。

```json
{ "branches": [ ... ], "changed": 3 }
```

#### `POST /api/pairs/{id}/branches/{branch}/push`

快进推送本地分支到远程。

```json
{ "branch": "main", "status": "pushed", "detail": "" }
```

非快进：`400`，detail 提示改用「同步」。

#### `POST /api/pairs/{id}/branches/{branch}/pull`

快进拉取远程分支到本地，响应同 push。

#### `POST /api/pairs/{id}/branches/{branch}/restore`

把分支恢复成备份版本（备份被删除前可用）。

```json
{ "branch": "main", "status": "restored", "detail": "已恢复到 main.bak.20261101080603" }
```

---

## 5. 操作层接口（Protocol）

```python
@dataclass(frozen=True)
class Endpoint:
    name: str          # local | remote
    url: str
    username: str = ""
    token: str = ""


class GitTransport(Protocol):
    def list_refs(self, ep: Endpoint) -> dict[str, str]: ...
    def workspace(self) -> GitWorkspace: ...


class GitWorkspace(Protocol):
    def fetch(self, ep: Endpoint, branch: str) -> str: ...
    def commit_time(self, sha: str) -> int: ...
    def push(self, ep: Endpoint, sha: str, branch: str) -> None: ...
    def overwrite(self, ep: Endpoint, sha: str, branch: str, *, expected: str) -> None: ...
    def delete(self, ep: Endpoint, branch: str) -> None: ...


class StateStore(Protocol):
    def create_session(self, sid: str, username: str, token: str) -> None: ...
    def get_session(self, sid: str) -> Session | None: ...
    def delete_session(self, sid: str) -> None: ...
    def add_pair(self, owner: str, local_repo: str, remote_url: str) -> Pair: ...
    def get_pair(self, pair_id: int) -> Pair | None: ...
    def list_pairs(self, owner: str) -> list[Pair]: ...
    def delete_pair(self, pair_id: int) -> None: ...
    def save_branches(self, pair_id: int, rows: list[BranchState]) -> None: ...
    def load_branches(self, pair_id: int) -> list[BranchState]: ...


class RepoProvider(Protocol):
    def verify_login(self, username: str, password: str) -> User: ...
    def issue_token(self, username: str, password: str) -> str: ...
    def get_user(self, token: str) -> User: ...
    def list_repos(self, token: str) -> list[Repo]: ...
    def get_repo(self, token: str, full_name: str) -> Repo: ...


class SecretVault(Protocol):
    def encrypt(self, plain: str) -> str: ...
    def decrypt(self, cipher: str) -> str: ...
```

### 错误类型

| 异常             | 触发条件                     | 映射状态码 |
| ---------------- | ---------------------------- | ---------- |
| `BranchNotFound` | 分支在源侧不存在             | 404        |
| `NonFastForward` | 快进推送被拒                 | 400        |
| `LeaseMismatch`  | 覆盖时远端已变化             | 409        |
| `AuthFailed`     | 凭据无效                     | 401 / 502  |
| `NetworkError`   | 超时、连接失败               | 502        |

---

## 6. 权限

| 情况           | 判断                                      |
| -------------- | ----------------------------------------- |
| 用户自己的仓库 | `repo.owner.login == session.username`    |
| 组织仓库       | `repo.owner.type == "Organization"` 时看 `repo.permissions.admin` |
| 协作者         | 既非 owner 又非 admin → `403`             |

字段来自 `GET /repos/{owner}/{repo}`，已实测：

```
owner.login  = alice
owner.type   = User
permissions  = { admin: true, push: true, pull: true }
```

---

## 7. 测试约定

- 操作层都有假实现：`FakeGitTransport`、`FakeStore`、`FakeProvider`
- 单元测试不依赖真实 Gitea、真实网络
- 集成测试用两个本地裸仓库模拟两端

---

*文档版本：v0.3 | 更新日期：2026-11-01*
