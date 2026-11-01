# gitsync 架构设计

本文说明设计取舍与分层。接口字段见 [开发文档](DEV.md)。

---

## 1. 铁律

1. **不删除分支。** 同步只移动指针，不创建、不删除业务分支。
2. **不产生新提交。** 不做 merge / rebase / cherry-pick。
3. **较新者胜。** 同一分支两端不同时，用提交较新的一侧覆盖较旧的一侧。
4. **覆盖前先备份。** 旧的一侧存为 `<branch>.bak.<时间>`，**每个分支只保留一份**；超过保留天数由同步自动清理。
5. **删备份前可恢复。** 备份还在时，可以把分支恢复成备份的版本。
6. **不碰本地端的数据目录。** 只通过 HTTP 和 git 协议交互。

## 2. 核心概念

| 概念                | 含义                                                           |
| ------------------- | -------------------------------------------------------------- |
| **本地端（local）** | 当前实现为 Gitea                                               |
| **远程端（remote）** | 任意 https git 地址                                            |
| **配对（pair）**    | 一个本地仓库 + 一个远程地址，一对一                            |
| **分支操作**        | commit / rollback / merge / rebase，对同步来说都是“指向变了”   |
| **备份**            | 被覆盖的旧版本另存为 `<branch>.bak.<时间>`，每个分支一份       |
| **对齐**            | 比时间、取较新、备份旧的、覆盖旧的一侧                         |

---

## 3. 同步规则

| 本地 | 远程     | 动作                                     |
| ---- | -------- | ---------------------------------------- |
| 有   | 无       | 复制到远程                               |
| 无   | 有       | 复制到本地                               |
| 有   | 有，相同 | 无                                       |
| 有   | 有，不同 | 较新者胜：旧的先备份，再被覆盖 |

不提供可选策略，一键同步就是这套规则。

每个分支只保留一份备份：生成新备份前先删掉该分支已有的 `.bak.*`。

备份保留天数存在 `repos.retention_days`（默认 7 天），同步时顺手清理过期的。

---

## 4. 权限

| 情况           | 判断                                                            |
| -------------- | --------------------------------------------------------------- |
| 用户自己的仓库 | `owner.login == 登录名`                                         |
| 组织仓库       | `owner.login` 是组织名，退化为看 `permissions.admin`            |
| 协作者         | `owner.login != 你` 且 `admin=False` → 拒绝                     |

字段来自 `GET /repos/{owner}/{repo}`：

```
owner.login = alice
owner.type  = User
permissions = { admin: true, push: true, pull: true }
```

---

## 5. git 操作层

操作层只负责“把一个 sha 从一个端点搬到另一个端点”，**不判断该不该搬**；判断在处理层。

```python
class GitTransport(Protocol):
    def list_refs(self, ep: Endpoint) -> dict[str, str]
    def workspace(self) -> GitWorkspace

class GitWorkspace(Protocol):
    def fetch(self, ep, branch) -> str
    def commit_time(self, sha) -> int
    def push(self, ep, sha, branch) -> None
    def overwrite(self, ep, sha, branch, *, expected) -> None
    def delete(self, ep, branch) -> None
```

### 操作组合

| 操作         | 组合                                                     |
| ------------ | -------------------------------------------------------- |
| 拉取整体状态 | `list_refs(local)` + `list_refs(remote)`                 |
| 单分支推送   | `fetch(local, b)` → `push(remote, sha, b)`               |
| 单分支拉取   | `fetch(remote, b)` → `push(local, sha, b)`               |
| 判断新旧     | `fetch` 两侧 → `commit_time` 比较                        |
| 备份旧版     | `fetch(旧侧, b)` → `push(旧侧, 旧 sha, b.bak.时间)`       |
| 对齐         | 备份旧侧 → `overwrite(旧侧, 新 sha, b, expected=旧 sha)`  |
| 从备份恢复   | `fetch(备份侧, bak)` → `overwrite(该侧, bak_sha, b, expected=当前 sha)` |
| 清理过期备份 | `list_refs` 找 `.bak.` → `delete`                        |

### 边界

- 用户可见的推送/拉取**只做快进**，非快进直接失败并提示用「同步」
- `overwrite` 只给引擎内部和「恢复」使用，且必须先有备份或明确的目标版本
- 不提供整仓强推、删除分支、重命名分支
- 全部操作在临时裸仓库里完成，用完即删

---

## 6. 分层与接口

```
┌─────────────────────────────────────────────┐
│ 前端 Vue ──▶ src/api.js ──▶ HTTP             │
└──────────────────────┬──────────────────────┘
┌──────────────────────▼──────────────────────┐
│ 后端接口层  api/  FastAPI 路由 + 请求模型     │
└──────────────────────┬──────────────────────┘
┌──────────────────────▼──────────────────────┐
│ 后端处理层  services/  比对、同步、选边       │
└───┬──────────────┬───────────────┬──────────┘
    ▼              ▼               ▼
 数据库层       Gitea API 调用层   git 操作层
 (Protocol)      (Protocol)        (Protocol)
                 + 凭据层
```

| 层 | 职责 | 对外接口 | 实现 |
| --- | --- | --- | --- |
| 前端 | 展示、触发、状态展示 | `src/api.js`、`Pair`/`Branch` | Vue 组件 |
| 后端接口层 | 参数校验、会话、序列化、权限 | FastAPI 路由 | `api/app.py` |
| 后端处理层 | 编排流程、判定该不该搬 | `SyncService` | `services/sync.py` |
| 数据库层 | 持久化 | `StateStore` | `adapters/store.py` |
| Gitea API 调用层 | 身份、仓库、权限 | `RepoProvider` | `adapters/gitea.py` |
| git 操作层 | 搬运提交对象 | `GitTransport` / `GitWorkspace` | `adapters/git_cli.py` |
| 凭据层 | 远程凭据加解密 | `SecretVault` | `adapters/vault.py` |

四条约束：

1. 接口层只解析请求、调处理层、序列化响应，不写业务逻辑
2. 处理层不出现 `subprocess` / `sqlite3` / `urllib`，只走 `ports/` 里的接口
3. 每个操作层都能换成别的实现，测试不需要真实 Gitea 或网络
4. 前端组件里不出现 `fetch`，统一走 `src/api.js`

接口清单、数据结构与函数表见 [模块与接口](MODULES.md)。

---

## 7. 认证

不自己建账号体系：

1. 登录页收用户名 + 密码
2. HTTP Basic 调 `GET /api/v1/user` 校验
3. 同一组凭据调 `POST /users/{username}/tokens` 创建令牌
4. 令牌写入 `sessions` 表，**密码用完即弃**
5. 签发 httponly session cookie
6. 两步验证用户额外提供动态码，放 `X-GITEA-OTP`

之后所有 API 和 git 操作都用该用户自己的令牌，权限与 Gitea 一致。

---

## 8. 远程凭据

远程仓库凭据跟着配对走：

- `remote_username` + `remote_secret_enc`
- Fernet 加密，密钥来自 `GITSYNC_SECRET` 或数据目录下的 `secret.key`
- 接口只返回 `has_remote_secret`

---

## 9. 非目标

- 不同步 issue / PR / release / wiki / packages
- 不做自动 merge、rebase、cherry-pick
- 不做整仓强制推拉
- 不做分支删除与重命名
- 不做公网可访问的服务

---

## 10. 后续再议

- 定时同步（APScheduler）
- 备份保留份数可配置（当前固定一份）
- include / exclude 分支过滤
- tag 同步
- 多远程目标
- `sessions.token_enc` 的密钥管理

---

*文档版本：v0.3 | 更新日期：2026-11-01*
