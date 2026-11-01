# gitsync

> 局域网 Gitea ⇄ 远程 Git 仓库的**分支级双向同步**工具

本地 Gitea 在局域网里当主力仓库，GitHub 放在公网做异地访问和备份。gitsync 把两边**按分支**对齐：只动分支指针、不删分支，提交不一致时用较新的一侧覆盖较旧的一侧。

---

## 它解决什么问题

Gitea 自带的 mirror 是**整仓 + 强制**语义：

- push mirror 等价 `git push --mirror`，会删掉远端独有的分支
- pull mirror 只能拉，不能推

典型事故：人在外面直接在 GitHub 上开了 `feature-x`，回家同步一次就被抹掉。

gitsync 的做法是**逐分支比较**：两边都有就看谁新，用较新的一侧覆盖较旧的一侧。

---

## 核心特性

| 特性               | 说明                                                   |
| ------------------ | ------------------------------------------------------ |
| **分支级**         | 只移动分支指针，不产生新提交                           |
| **双向**           | 哪边落后补哪边，没有“只推”“只拉”开关                   |
| **较新者胜**       | 提交不一致时比较时间，用较新的一侧覆盖较旧的一侧       |
| **备份与恢复**     | 覆盖前把旧的一侧存成 `<branch>.bak.<时间>`，每个分支保留一份；保留天数可配，到期自动清理；删除前可恢复 |
| **状态持久化**     | 表格数据存在后端，进页面直接看到上次结果和更新时间     |
| **权限跟随 Gitea** | 用 Gitea 账号登录，只能看到并操作自己有权限的仓库      |
| **凭据加密**       | 远程仓库令牌用 Fernet 加密存储，接口不返回密文         |

---

## 使用流程

1. 用 **Gitea 账号密码**登录（密码不落库，换一个令牌后即丢弃）
2. 左侧选仓库 → 右侧填远程地址（私有仓库再填令牌）
3. 点 **比对** 查看两端分支状态（只读）
4. 点 **同步** 按规则对齐
5. 单个分支也可以单独**推送 / 拉取**（只做快进）

界面示例：

```
仓库                         zxf/project
● zxf/project     私有      远程  github.com/you/project.git
  zxf/another-project                凭据  已保存
  未配置同步                 最后更新 2026-11-01 16:06:03

                            本地分支  远程分支  状态     备注        操作
                            main      main      synced              —
                            dev                 pushed               [推送]
```

---

## 快速开始

### 后端

```bash
cd backend
python3 -m venv .venv
.venv/bin/pip install -e ".[web,dev]"
cp config.example.toml config.toml   # 填 Gitea 地址
chmod 600 config.toml
.venv/bin/uvicorn gitsync.api.app:app --host 127.0.0.1 --port 8000 --reload
```

### 前端

```bash
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173，/api 自动代理到后端
```

### CLI

```bash
cd backend
.venv/bin/gitsync repos
.venv/bin/gitsync repo add demo --local alice/demo --remote https://github.com/you/demo.git
.venv/bin/gitsync status demo --live
.venv/bin/gitsync sync demo
```

---

## 项目结构

```
gitsync/
├── backend/
│   └── src/gitsync/
│       ├── api/          后端接口层（路由、会话）
│       ├── services/     后端处理层（同步、选边）
│       ├── ports/        各层接口定义
│       ├── adapters/     数据库 / Gitea / git / 凭据实现
│       └── cli.py        命令行
├── frontend/         Vue 3 + Vite
├── docs/
│   ├── ARCHITECTURE.md   设计取舍与分层
│   ├── MODULES.md        接口、数据结构、函数表
│   ├── DEV.md            字段、接口、错误码
│   └── FRONTEND.md       前端模型与组件
└── README.md
```

---

## 技术栈

- 后端：Python 3.11+、FastAPI、SQLite、系统 git
- 前端：Vue 3、Vite
- 认证：Gitea 账号密码 → 访问令牌
- 加密：Fernet（远程凭据）

---

## 文档

| 文档                             | 内容                                             |
| -------------------------------- | ------------------------------------------------ |
| [架构设计](docs/ARCHITECTURE.md) | 问题背景、铁律、同步规则、分层与接口、git 操作层 |
| [模块与接口](docs/MODULES.md) | 按层列出的接口、数据结构与函数表 |
| [前端文档](docs/FRONTEND.md) | 数据模型、组件、数据流与显示规则 |
| [开发文档](docs/DEV.md)          | 后端接口、数据模型字段、错误码、Protocol 签名    |

---

## 当前状态

| 能力                              | 状态                     |
| --------------------------------- | ------------------------ |
| 登录换令牌、会话、按用户隔离      | 已实现                   |
| 仓库列表、配对、远程凭据加密      | 已实现                   |
| 状态持久化 + 最后更新时间         | 已实现                   |
| 比对、同步、备份                  | 已实现                   |
| 从备份恢复                        | 未开始                   |
| 单分支推送 / 拉取                 | 已实现（待改为严格快进） |
| 操作层抽成 Protocol               | 待重构                   |
| 权限收紧、定时同步、分支过滤、tag | 未开始                   |

**参考版本：Gitea 28.2.0**

---

*文档版本：v1.1 | 更新日期：2026-11-01*
