# hermes-sync

语言：[English](README.md) | 简体中文

`hermes-sync` 是 Hermes Agent profile 的本地优先同步插件，风格类似
Obsidian Sync。它把同步策略放在 Hermes core 之外，并且只同步明确允许的
数据范围。

## 目标

每台设备都保留自己的本地 Hermes profile。插件会把安全、应用感知的数据对象
导出到同步层，通过选定的远端后端交换这些对象，再导入到其他设备。它不会把
整个 profile 目录当成网盘目录镜像。

## 非目标

- 不 fork Hermes core。
- 不同步整个 `~/.hermes` 目录。
- 不以文件方式同步 SQLite 状态数据库。
- 默认不同步 `.env`、API key、token 或 provider 凭证。
- 不同步日志、缓存、tmp、lock 文件或运行时状态。
- 初始阶段不引入复杂托管云服务。

## 插件结构

```text
hermes-sync/
  AGENTS.md
  AGENT.md
  README.md
  README.zh-CN.md
  feature_list.json
  plugin.yaml
  scripts/
    install_dev_plugin.py
  docs/
    architecture.md
    sync-scopes.md
    harness.md
    deployment.md
    hermes-agent-integration.md
    feature-list.md
    progress.md
    implementation-plan.md
```

实现模块按这个形状组织：

```text
hermes_sync/
  __init__.py
  cli.py
  sync_engine.py
  scheduler.py
  session_snapshots.py
  manifest.py
  scopes.py
  remotes/
    local.py
    oss.py
    webdav.py
    s3.py
```

## 用户命令

当前可用的 slash commands：

```text
/sync status
/sync now
/sync pause
/sync resume
/sync conflicts
```

当前注册的 tools：

- `sync_status`
- `sync_now`
- `sync_list_conflicts`
- `sync_restore_version`

计划中的顶层 CLI 还需要 Hermes core 提供通用插件 CLI-command bridge：

```bash
hermes sync setup
hermes sync status
hermes sync push
hermes sync pull
hermes sync once
hermes sync --continuous
hermes sync conflicts
hermes sync restore
```

## 当前实现

已实现的能力：

- 注册 `/sync status`。
- 注册 `/sync now`、`/sync pause`、`/sync resume` 和 `/sync conflicts`。
- 注册 `sync_status`、`sync_now`、`sync_list_conflicts` 和
  `sync_restore_version`。
- 创建 `device.json`。
- 初始化 `manifest.sqlite`。
- 按配置 scope 扫描候选对象，不上传或导入未允许的数据。
- 实现本地文件夹 `RemoteBackend`。
- 把待上传对象暂存到 `sync/outbox`。
- 导入前先把远端对象暂存到 `sync/inbox`。
- 对支持的 config、artifact、session snapshot 对象执行 `push`、`pull`
  和 `once`。
- 报告增量同步指标，包括 dirty/unchanged 对象数、hash cache 复用、
  uploaded bytes 和各阶段耗时。
- 通过只读 SQLite 查询从 `state.db` 导出 session snapshots，并把拉取到的
  snapshots 存到插件自有的 `sync/sessions/` 历史目录。
- 通过 manifest 和远端 tombstones 显式传播删除。
- 对非重叠 JSON/YAML 对象修改和 UTF-8 文本行修改进行合并；重叠文本和
  二进制 artifact 修改会生成 pending conflict 记录和插件自有 conflict 副本。
- 把本地版本历史存到 `sync/versions`，并通过 `sync_restore_version` 恢复
  artifact/config 旧版本。
- 运行有边界的连续同步 worker，并把 pause/watcher 状态放在插件自有本地
  sync 元数据里。
- 通过 session/tool hooks 唤醒连续 worker，对突发事件 debounce，按允许
  scope 轮询 config/artifact mtime，并用本地插件锁阻止重叠同步周期。
- 通过共享 backend conformance harness 覆盖 local-folder、OSS、WebDAV、
  S3/R2 后端。
- 支持 `remote: oss`，通过 Alibaba Cloud OSS 兼容 S3 API；实现已通过 fake
  conformance，真实 Alibaba Cloud acceptance 是手动 gate。
- 支持 `remote: webdav`，覆盖 MKCOL、PROPFIND、PUT、GET、DELETE 子集。
- 支持 `remote: s3` 和 `remote: r2`，覆盖标准 S3-compatible 请求子集。
- 顶层 `hermes sync ...` 暂时仍等待 Hermes core 支持通用插件 CLI bridge。

## 安装和使用

`hermes-sync` 当前是开发版目录插件安装。你需要本地 checkout 这个仓库，
安装后的 plugin shim 会指向这个 checkout。安装后不要移动或删除源码目录；
如果移动了，需要重新安装。

```bash
git clone https://github.com/alovwang-sys/hermes-sync.git
cd hermes-sync
```

把新 checkout 用到真实 profile 前，建议先跑 harness：

```bash
python3 -m harness.run
```

### 最快本地烟测

最短安全路径是一条命令完成：安装插件、启用插件、写入本地文件夹远端配置。

```bash
python3 scripts/install_dev_plugin.py --profile ~/.hermes --enable-local
```

这条命令会写入：

```text
~/.hermes/plugins/hermes-sync/plugin.yaml
~/.hermes/plugins/hermes-sync/__init__.py
```

同时会把安全的本地 remote 写入 `~/.hermes/config.yaml`：

```yaml
plugins:
  enabled:
    - hermes-sync

sync:
  remote: local
  remote_path: /tmp/hermes-sync-dev-remote
  scopes:
    config: true
    sessions: false
    artifacts: true
    memory: false
    skills: false
    plugins: false
    secrets: false
```

如果 `config.yaml` 已经存在，安装器会先创建带时间戳的备份。如果 profile
里已经有顶层 `sync:` block，默认不会覆盖；显式传入
`--replace-sync-config` 才会替换：

```bash
python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-local \
  --replace-sync-config
```

需要自定义本地 remote 目录时：

```bash
python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-local \
  --remote-path /path/to/hermes-sync-remote
```

### 手动安装

如果只想安装插件文件，不让安装器改 `config.yaml`：

```bash
python3 scripts/install_dev_plugin.py --profile ~/.hermes
```

然后手动把 `hermes-sync` 和 `sync:` block 加到 `~/.hermes/config.yaml`：

```yaml
plugins:
  enabled:
    - hermes-sync

sync:
  remote: local
  remote_path: /tmp/hermes-sync-dev-remote
  scopes:
    config: true
    sessions: false
    artifacts: true
    memory: true
    skills: true
    plugins: true
    secrets: false
```

真实 profile 第一次烟测建议使用更窄的 quick-start scope：
`config: true`、`artifacts: true`，其他全部 false。

### 运行时加载

新安装插件或修改配置后，Hermes 需要重启，或者由 Hermes core 触发 plugin
rediscovery，之后 `/sync` 和 `sync_*` tools 才会注册。当前插件本身不保证
已运行 Hermes 进程内的真正热插拔。开发版 shim 会从当前 checkout 导入
Python 模块，所以修改插件代码后，重启 Hermes 是最可靠路径。

### 日常命令

在 Hermes 里使用 slash commands：

```text
/sync status
/sync now
/sync conflicts
/sync pause
/sync resume
```

顶层 `hermes sync ...` CLI 仍是后续工作，要等 Hermes core 提供通用插件
CLI-command bridge。

注册给 Hermes 的 tools：

- `sync_status`
- `sync_now`
- `sync_list_conflicts`
- `sync_restore_version`

## 会同步什么

同步按 scope 工作，不做整个目录镜像。

| Scope | 默认 | 说明 |
| --- | --- | --- |
| `config` | 开启 | 同步非密钥配置文件，例如 `config.yaml`。secret-like keys 会被跳过。 |
| `artifacts` | 开启 | 同步 `artifacts/`、`outputs/` 和 `reports/` 下允许的文件。 |
| `sessions` | 关闭 | 从 `state.db` 导出只读 JSON snapshots；不会同步 SQLite 文件。Session 文本可能包含用户内容。 |
| `memory` | 关闭 | 同步 `memories/` 下允许的文件。 |
| `skills` | 关闭 | 同步 skill 文件，同时排除 skill 运行时状态。 |
| `plugins` | 关闭 | 只同步 plugin manifests；plugin 可执行代码和缓存留在本地。 |
| `secrets` | 关闭 | 默认不支持。真实 profile 不要启用。 |

即使打开了较宽的 scope，以下路径也会被阻止：`.env`、API keys、tokens、
provider credentials、`state.db`、`state.db-wal`、`state.db-shm`、日志、
缓存、tmp 文件、lock 文件和插件自有的 `sync/` 元数据。

## 双设备本地测试

先用两个隔离 profile 和同一个 `remote_path` 验证 push/pull，再切云端：

```bash
python3 scripts/install_dev_plugin.py \
  --profile /tmp/hermes-device-a \
  --enable-local \
  --remote-path /tmp/hermes-sync-dev-remote

python3 scripts/install_dev_plugin.py \
  --profile /tmp/hermes-device-b \
  --enable-local \
  --remote-path /tmp/hermes-sync-dev-remote
```

用 device A 启动 Hermes，创建或修改一个允许同步的 artifact，然后运行：

```text
/sync now
```

再用 device B 启动 Hermes，运行：

```text
/sync status
/sync now
```

确认 device B 只出现允许的 config/artifact 对象。运行时状态、数据库、凭证、
日志、缓存和 lock 文件不应该出现。

## 切换到云端远端

本地文件夹 remote 跑通后，可以让安装器自动写云端 routing 配置。凭证放在
环境变量里，不写入同步的 profile config。

### Alibaba Cloud OSS

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=...
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=...

python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-sync \
  --remote oss \
  --bucket your-hermes-sync-bucket \
  --endpoint https://s3.oss-cn-hangzhou.aliyuncs.com \
  --region cn-hangzhou \
  --prefix hermes-sync/default-profile \
  --replace-sync-config
```

可选 STS token：

```bash
export ALIBABA_CLOUD_SECURITY_TOKEN=...
```

### Cloudflare R2

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-sync \
  --remote r2 \
  --bucket your-hermes-sync-bucket \
  --endpoint https://account-id.r2.cloudflarestorage.com \
  --prefix default-profile \
  --replace-sync-config
```

### S3-compatible

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=... # optional

python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-sync \
  --remote s3 \
  --bucket your-hermes-sync-bucket \
  --endpoint https://s3.example.com \
  --region us-east-1 \
  --prefix default-profile \
  --replace-sync-config
```

### WebDAV

```bash
export HERMES_SYNC_WEBDAV_USERNAME=...
export HERMES_SYNC_WEBDAV_PASSWORD=...

python3 scripts/install_dev_plugin.py \
  --profile ~/.hermes \
  --enable-sync \
  --remote webdav \
  --url https://webdav.example.com/hermes-sync \
  --prefix default-profile \
  --replace-sync-config
```

支持的 `--remote` 值：

- `local`
- `oss`
- `webdav`
- `s3`
- `r2`

第一次云端烟测建议保持 `sessions: false` 和 `secrets: false`。如果确认远端
bucket/path、数据策略和同步结果都符合预期，再按需加：

```bash
--include-sessions
--include-memory
--include-skills
--include-plugin-manifests
```

安装器永远不会启用 `secrets`。

## OSS 远端配置说明

OSS 凭证不要写进 `config.yaml`，只放在本地环境变量：

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=...
export ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
export ALIBABA_CLOUD_SECURITY_TOKEN=... # optional STS token
```

profile config 只包含非密钥 routing 数据：

```yaml
sync:
  remote: oss
  bucket: your-hermes-sync-bucket
  endpoint: https://s3.oss-cn-hangzhou.aliyuncs.com
  region: cn-hangzhou
  prefix: hermes-sync/default-profile
```

默认 harness 使用带临时 prefix 的 unsigned fake OSS 服务。真实 OSS bucket
不要使用 harness-only 的 `unsigned` 或 `path_style` 设置。中国大陆区域的新
OSS 用户可能需要自定义域名进行数据 API 操作；把该域名放在 `endpoint`，
凭证仍然只放环境变量。

只有在你明确提供真实 bucket 和本地凭证时，才运行 gated live acceptance：

```bash
export HERMES_SYNC_OSS_BUCKET=...
export HERMES_SYNC_OSS_ENDPOINT=https://s3.oss-cn-hangzhou.aliyuncs.com
export HERMES_SYNC_OSS_REGION=cn-hangzhou
python3 -m harness.oss_live_acceptance
```

## WebDAV 远端配置说明

WebDAV 用户名和密码不要写进 `config.yaml`。如果服务端需要认证，放在本地
环境变量里：

```bash
export HERMES_SYNC_WEBDAV_USERNAME=...
export HERMES_SYNC_WEBDAV_PASSWORD=...
```

profile config 只包含非密钥 routing 数据：

```yaml
sync:
  remote: webdav
  url: https://webdav.example.com/hermes-sync
  prefix: default-profile
```

默认 harness 使用临时 prefix 下的无认证 fake WebDAV 服务，只在本地验证协议
子集，不会联系真实 WebDAV 服务器。

## S3/R2 远端配置说明

对于 Cloudflare R2 等通用 S3-compatible remotes，access keys 不要写进
`config.yaml`，放在本地环境变量：

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=... # optional
```

profile config 只包含非密钥 routing 数据：

```yaml
sync:
  remote: r2
  bucket: your-hermes-sync-bucket
  endpoint: https://account-id.r2.cloudflarestorage.com
  region: auto
  prefix: default-profile
```

标准 S3-compatible 服务使用 `remote: s3`。默认 harness 使用带临时 prefix 的
unsigned fake S3-compatible 服务，不会联系真实云存储。

## 项目追踪

- `feature_list.json` 是机器可读的功能清单、进度来源和 harness 执行契约。
- `docs/architecture.md` 定义插件边界、对象模型、backend contract、
  session sync 和 conflict model。
- `docs/sync-scopes.md` 定义明确的同步 scopes 和默认排除规则。
- `docs/harness.md` 定义可执行 harness contract 和必需场景。
- `docs/deployment.md` 定义开发安装和测试部署 checklist。
- `docs/hermes-agent-integration.md` 记录本地 Hermes Agent checkout 的兼容性。
- `docs/feature-list.md` 是功能清单和 harness 覆盖矩阵。
- `docs/progress.md` 跟踪当前实现和 harness 进度。
- `docs/implementation-plan.md` 跟踪里程碑顺序和退出条件。

## 参考

- OpenAI Codex `AGENTS.md`: https://developers.openai.com/codex/guides/agents-md
- OpenAI Codex plugin docs: https://developers.openai.com/codex/plugins/build
- OpenAI apply-patch harness guidance: https://developers.openai.com/api/docs/guides/tools-apply-patch
- OpenAI shell tool harness guidance: https://developers.openai.com/api/docs/guides/tools-shell
- OpenAI Docs MCP: https://developers.openai.com/learn/docs-mcp
- Obsidian Headless Sync: https://help.obsidian.md/sync/headless
- Obsidian Sync settings: https://help.obsidian.md/sync/settings
- Obsidian Sync troubleshooting: https://help.obsidian.md/sync/troubleshoot
