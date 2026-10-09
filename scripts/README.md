# 通用 Git 同步

目标是一次调用完成推送与远端核对，内部最多三次网络尝试。网络、认证或远端策略仍可能阻止同步，成功只依据远端目标分支的实际 SHA。

## 当前项目

```sh
uv run --no-sync python scripts/git_sync.py --remote origin --branch main
```

## 本机所有项目

源文件是本仓库的 `scripts/git_sync.py`；安装副本位于 `${CODEX_HOME:-~/.codex}/scripts/git_sync.py`，更新源文件后同步安装副本。全局 `AGENTS.md` 规定 Codex 在授权的提交与推送任务中使用它；其他项目无需复制脚本。

已安装的 Git 别名：

```sh
git sync-push
```

默认沿用当前分支的 upstream 远端及目标分支。首次推送无 upstream 时明确指定已有远端：

```sh
git sync-push --remote origin --set-upstream
```

在另一台机器安装时，通过 `uv run python` 将源文件复制到其 Codex home 的 `scripts/git_sync.py`，并设置别名：

```sh
git config --global alias.sync-push '!uv run --no-sync python "${CODEX_HOME:-$HOME/.codex}/scripts/git_sync.py"'
```

没有 `uv` 的机器需先安装 `uv`；使用非默认 Codex home 时沿用该机器的实际路径。全局指令需要在新会话中加载；项目内更具体的指令优先。

## 执行过程

- 固定本次提交 SHA、当前分支和唯一 push URL，只推这一个目标；不会自动暂存、提交、合并或强推。
- HTTP 沿用该 URL 的有效 Git 版本/低速配置；未配置时使用 HTTP/1.1 和低速退出。首次沿用正常解析，网络故障重试时重新解析当前 IPv4 地址并临时指定。SSH、file 和本地路径使用原传输，保持认证与代理配置。
- 每次推送默认最多 45 秒，核对默认最多 20 秒；DNS 最多 8 秒。重试前间隔 3 / 8 秒。超时终止整个传输进程组，防止上一轮仍在推送。
- 每次都从实际 push URL 核对目标分支 SHA；即使推送响应丢失，核对成功也能识别已同步状态。无法核对时不会报告成功。
- 仅连接、DNS、传输和服务暂时故障重试；认证、权限、分支保护、非快进、证书及未知错误直接报告。普通项目不会被指向本库的 GitHub 仓库，也不会使用本库提交身份。
- 每次重试前检查本地 HEAD、当前分支和 push URL 没有改变；共享工作区的其他文件改动保持原状。`--set-upstream` 仅在远端核对成功后设置本地追踪；设置失败另列警告。
- JSON 输出记录提交、目标、实际尝试及核对结果。没有实际统计时，不宣称首次连接成功率。

GitHub 专用全局/本库配置为 `http.https://github.com/.version=HTTP/1.1`、`lowSpeedLimit=1`、`lowSpeedTime=20`。不永久固定 IP；不修改现有身份、凭据、TLS 验证和其他项目远端。现有全局 `postBuffer` 不作为连接修复手段。

## 依据

- [Git 配置](https://git-scm.com/docs/git-config)：HTTP 版本、低速退出、临时地址解析。
- [Git push](https://git-scm.com/docs/git-push)：显式源/目标 refspec。
- [Codex 全局 AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md)：全局默认与项目覆盖。
