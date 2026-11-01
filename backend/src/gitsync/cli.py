"""命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .adapters.config import ConfigError, load_config
from .adapters.git_cli import CliGitTransport
from .adapters.gitea import GiteaProvider
from .adapters.store import Store
from .adapters.vault import FernetVault
from .ports import ProviderError
from .services.sync import Outcome, SyncService


def _store(cfg_path):
    cfg = load_config(cfg_path)
    return cfg, Store(cfg.db_path)


def _service(cfg, store) -> SyncService:
    """装配处理层。"""
    return SyncService(cfg, store, FernetVault(cfg.db_path.parent), CliGitTransport(cfg.temp_dir))


def cmd_repo_add(args) -> int:
    """登记一个配对。"""
    cfg, store = _store(args.config)
    try:
        repo = store.add_pair(
            args.name,
            args.local,
            args.remote,
            push=not args.no_push,
            pull=not args.no_pull,
            retention_days=args.retention,
        )
    except (ValueError, KeyError) as exc:
        print(f"添加失败：{exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
    print(f"已添加 {repo.name}：{repo.local_repo} ⇄ {repo.remote_url}")
    return 0


def cmd_repo_list(args) -> int:
    """列出已登记的配对。"""
    _, store = _store(args.config)
    repos = store.list_pairs()
    store.close()
    if not repos:
        print("还没有仓库，用 repo add 添加。")
        return 0
    for repo in repos:
        flags = []
        if not repo.push_enabled:
            flags.append("no-push")
        if not repo.pull_enabled:
            flags.append("no-pull")
        flags.append(f"retention={repo.backup_retention_days}d")
        print(f"{repo.name:<16} {repo.local_repo:<30} {repo.remote_url}  ({', '.join(flags)})")
    return 0


def cmd_repo_remove(args) -> int:
    """删除配对记录，不动仓库。"""
    _, store = _store(args.config)
    try:
        store.delete_pair(args.name)
    except KeyError as exc:
        store.close()
        print(str(exc), file=sys.stderr)
        return 2
    store.close()
    print(f"已删除 {args.name}")
    return 0


def cmd_repos(args) -> int:
    """列出本地端（Gitea）里当前账号能看到的仓库。"""
    cfg = load_config(args.config)
    provider = GiteaProvider(cfg.local_url)
    try:
        user = provider.get_user(cfg.local_token)
        repos = provider.list_repos(cfg.local_token)
    except ProviderError as exc:
        print(f"连接失败：{exc}", file=sys.stderr)
        return 2
    print(f"账号：{user.login}（{user.full_name or '-'}）")
    print(f"仓库：{len(repos)} 个")
    for repo in repos:
        flags = []
        if repo.private:
            flags.append("private")
        if repo.empty:
            flags.append("空仓")
        if repo.mirror:
            flags.append("镜像")
        if not repo.push:
            flags.append("只读")
        print(f"  {repo.full_name:<32} {' '.join(flags)}")
    return 0


def cmd_sync(args) -> int:
    """执行同步；有失败项时返回码为 1，方便放进定时任务。"""
    cfg, store = _store(args.config)
    repos = store.list_pairs()
    if args.repo:
        try:
            repos = [store.get_pair(args.repo)]
        except KeyError as exc:
            store.close()
            print(str(exc), file=sys.stderr)
            return 2

    service = _service(cfg, store)
    outcomes: list[Outcome] = []
    for repo in repos:
        outcomes.extend(service.sync_pair(repo))
    store.close()

    if args.json:
        print(json.dumps([o.__dict__ for o in outcomes], ensure_ascii=False, indent=2))
    else:
        if not outcomes:
            print("没有可同步的分支。")
        for outcome in outcomes:
            line = f"{outcome.repo:<14} {outcome.branch:<26} {outcome.status:<10}"
            if outcome.detail:
                line += f" {outcome.detail}"
            print(line)
        bad = [o for o in outcomes if not o.ok]
        print(f"\n共 {len(outcomes)} 个分支，{len(bad)} 个需要关注")
    return 0 if all(o.ok for o in outcomes) else 1


def cmd_status(args) -> int:
    """查看状态；--live 走只读比对。"""
    cfg, store = _store(args.config)
    repos = store.list_pairs()
    if args.repo:
        try:
            repos = [store.get_pair(args.repo)]
        except KeyError as exc:
            store.close()
            print(str(exc), file=sys.stderr)
            return 2
    if args.live:
        service = _service(cfg, store)
        outcomes: list[Outcome] = []
        for repo in repos:
            outcomes.extend(service.compare(repo))
        store.close()
        print("只读对比（不会修改任何东西）：")
        for o in outcomes:
            line = f"{o.repo:<14} {o.branch:<26} {o.status:<10}"
            if o.detail:
                line += f" {o.detail}"
            print(line)
        return 0

    rows = []
    for repo in repos:
        for row in store.list_branches(repo.id):
            rows.append(
                {
                    "repo": repo.name,
                    "branch": row.branch,
                    "status": row.status,
                    "local": row.local_sha,
                    "remote": row.remote_sha,
                    "backup": row.backup_branch,
                    "detail": row.detail,
                }
            )
    store.close()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not rows:
        print("还没有同步记录。")
        return 0
    for row in rows:
        short = lambda v: (v or "-")[:8]
        print(f"{row['repo']:<14} {row['branch']:<26} {row['status']:<10} "
              f"local={short(row['local']):<9} remote={short(row['remote'])}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitsync", description="本地 Gitea 与远程 Git 仓库的分支级双向同步"
    )
    parser.add_argument("--config", "-c", help="配置文件路径")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    repo = sub.add_parser("repo", help="管理仓库")
    repo_sub = repo.add_subparsers(dest="repo_cmd", required=True)

    add = repo_sub.add_parser("add", help="添加仓库")
    add.add_argument("name")
    add.add_argument("--local", required=True, help="owner/name")
    add.add_argument("--remote", required=True, help="远程 git 地址")
    add.add_argument("--no-push", action="store_true")
    add.add_argument("--no-pull", action="store_true")
    add.add_argument("--retention", type=int, default=7, help="归档保留天数")
    add.set_defaults(func=cmd_repo_add)

    ls = repo_sub.add_parser("list", help="列出仓库")
    ls.set_defaults(func=cmd_repo_list)

    rm = repo_sub.add_parser("remove", help="删除仓库")
    rm.add_argument("name")
    rm.set_defaults(func=cmd_repo_remove)

    repos = sub.add_parser("repos", help="列出本地端（Gitea）的仓库")
    repos.set_defaults(func=cmd_repos)

    sync = sub.add_parser("sync", help="同步一轮")
    sync.add_argument("repo", nargs="?")
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(func=cmd_sync)

    status = sub.add_parser("status", help="查看同步状态")
    status.add_argument("repo", nargs="?")
    status.add_argument("--live", action="store_true", help="实时对比两端，不写任何东西")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except ConfigError as exc:
        print(f"配置错误：{exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已中断", file=sys.stderr)
        return 130
