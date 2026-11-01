# gitsync backend

gitsync 的后端：分支级同步引擎，后续会加上 HTTP API 和登录。

同步规则、设计取舍见仓库根目录的 [README.md](../README.md)。

## 开发

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## 测试

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## 启动 API

```bash
.venv/bin/uvicorn gitsync.api.app:app --host 127.0.0.1 --port 8000 --reload
```

配置默认读当前目录的 `config.toml`，也可以用环境变量 `GITSYNC_CONFIG` 指定。

## 用法

```bash
.venv/bin/gitsync --version
.venv/bin/gitsync -c config.toml repo add NAME --local owner/name --remote <git-url>
.venv/bin/gitsync -c config.toml sync
```
