# 客户快速部署准备

第一阶段不把本机演示环境暴露给客户。客户现场由演示者本人操作；客户决定采购后，从全新的 `customer-template` 开始部署，不能复制 `local-demo` 的数据库、卷、密钥或账号。

## 可移植交付基线

- Web、API、Worker 镜像同时构建 `linux/amd64` 与 `linux/arm64`。
- PostgreSQL、Nginx 与工具镜像使用多架构官方镜像。
- 数据库变更由 Alembic 向前迁移，启动时以一次性迁移容器执行。
- 配置通过非密钥环境文件传入，密钥通过文件挂载；不依赖开发者个人路径。
- 客户业务数据与文件使用独立 Compose project 和卷。

## 在线快速部署流程

1. 获取客户批准的 Linux 服务器、域名、TLS 证书与网络策略。
2. 从已经 `production-images` 审批的 Workflow Run 下载 `executive-ai-release-<version>-<commit>-<run_id>` Artifact，取得 `release-bundle.json` 和 `release-bundle.sigstore.json`；不得只根据镜像 Tag 自行拼装交付版本。
3. 复制 `compose.yml`、`deploy/`、`scripts/` 与对应文档，不复制本机 `runtime/`、`backups/`。
4. 运行 `prepare-env.sh customer-template` 生成客户独立密钥，并将两个 release bundle 文件复制到 `runtime/customer-template/release/`。
5. 在正式上线前增加 TLS 终止、反向代理信任范围和客户批准的监听地址；Phase 1 的 loopback guard 不能直接绕过。
6. 安装 `cosign` 和 `jq`。从已验收 bundle 中把 `release.version`、`release.gitCommit`、`database.alembicHead` 和 `images` 六个完整值分别写入客户 `.env` 的 `RELEASE_VERSION`、`RELEASE_GIT_COMMIT`、`EXPECTED_ALEMBIC_HEAD`、`WEB_IMAGE`、`API_IMAGE`、`WORKER_IMAGE`、`POSTGRES_IMAGE`、`NGINX_IMAGE` 和 `FILE_TOOL_IMAGE`。不得抄写或截断 digest。
7. 执行 `./scripts/start-release.sh customer-template`。脚本先验证 release bundle 的 Sigstore 签名、透明日志证据以及 GitHub OIDC 中的仓库、commit SHA、ref 和触发类型；再对六个镜像 digest 与 Alembic head 做逐项精确比对，并检查三个应用镜像签名的版本/commit/组件注解。任一字段不一致就会在拉取或迁移前停止。
8. 交互创建首位企业管理员，配置组织与事业部范围。
9. 执行安全、权限、备份恢复、日志和核心业务验收。

`start.sh` 只用于本机开发与现场演示构建；客户交付不得使用它。`start-release.sh` 不解析可变 Tag，只使用签名 bundle 中已批准的 digest，并强制 `--no-build`。这意味着 Web、API、Worker 不能跨版本混搭，基础镜像与迁移契约也不能被临时替换。

该结构让工程人员不需重做应用即可从 Mac 演示切换到客户 Linux。正式客户部署仍必须经过网络、域名、证书、数据源、合规与运维确认，不能把“能启动”当成“已上线”。

## 采购前应向客户确认

- 服务器架构、操作系统、CPU/内存/磁盘与备份介质
- 是否内网、专有云或公有云，是否允许访问镜像仓库
- 域名、证书、统一身份 OIDC/SSO 与密码策略
- 组织/事业部数据范围、管理员与 FDE 边界
- 数据源、文件保留、审计保留、日志脱敏与等保要求
- RPO/RTO、监控告警、升级窗口和责任人

## 离线部署边界

离线镜像包、依赖清单、校验签名、离线升级与回滚工具本阶段明确不制作。第五阶段结束后再基于已稳定的镜像、数据库迁移、配置契约与客户安全要求形成正式离线包，避免现在固化尚未稳定的依赖。
