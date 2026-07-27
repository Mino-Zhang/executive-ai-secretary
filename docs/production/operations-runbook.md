# 日常运维手册

## 每次演示前

```bash
git status --short --branch
./scripts/start.sh local-demo
./scripts/status.sh local-demo
./scripts/smoke-test.sh local-demo
./scripts/sync-now.sh local-demo demo-enterprise
./scripts/backup.sh local-demo pre-demo
```

确认页面显示“本机脱敏演示环境”，不存在客户真实数据，且浏览器访问的是 `127.0.0.1:8080`。

## 日志

```bash
./scripts/logs.sh local-demo
./scripts/logs.sh local-demo api
./scripts/logs.sh customer-template worker
```

Docker 日志默认单文件 10 MB、最多 5 个并压缩。结构化字段至少包括时间、级别、组件、请求 ID；Nginx 增加状态码、时延与上游时延。日志不得包含密码、Session、CSRF、文件密钥、正文或完整上传内容。

## 健康检查

- `/_gateway/health`：网关进程
- `/health/live`：API 进程存活
- `/health/ready`：API、数据库、迁移就绪

就绪失败时按顺序查看：

```bash
./scripts/status.sh local-demo
./scripts/logs.sh local-demo migrate
./scripts/logs.sh local-demo postgres
./scripts/logs.sh local-demo api
```

## 停止与重启

```bash
./scripts/stop.sh local-demo
./scripts/start.sh local-demo
```

普通 `down` 保留卷。禁止对有价值环境执行 `docker compose down -v`、`docker volume prune` 或未经确认的目录删除。

## 更新版本

1. 记录当前 Git revision 与镜像版本。
2. 完成并验证备份。
3. 现场 Demo 可检出已审阅 commit；客户环境必须取得受保护发布 Job 产生的 `release-bundle.json` 与 `release-bundle.sigstore.json`，记录 Workflow Run URL 和审批人。
4. 将 bundle 中的版本、commit、Alembic head 和六个镜像 digest 逐项写入客户 `.env`，由第二人核对；禁止使用 Tag 或从不同 bundle 复制值。
5. Demo 执行 `./scripts/start.sh local-demo`；客户环境执行 `./scripts/start-release.sh customer-template`。两条路径都按角色初始化、迁移、权限重放、常驻服务的顺序启动，客户路径会先验证签名 bundle 与镜像签名，并额外拒绝源码构建。
6. 执行 smoke test 和核心登录/会话/文件权限回归。
7. 失败时根据迁移兼容性选择应用回滚或数据库恢复，不得盲目降级。

## 容量与保留

每周检查 Docker 卷、`backups/` 与宿主机剩余空间。第一阶段默认不自动删除备份；制定客户保留策略后再加入受审计的清理任务。

## Worker 租约、重试与死信

Worker 领取任务时在一个短事务内完成 `queued -> running`、尝试计数累加、`JobAttempt` 创建和租约 token 写入。处理期间由独立短事务按 `WORKER_HEARTBEAT_SECONDS` 续租；心跳必须小于 `WORKER_LEASE_SECONDS`。完成、失败、取消和回收都需同时匹配当前 owner 与 token，旧 Worker 不能覆盖新尝试。所有租约边界使用数据库时钟。

过期任务会关闭当前尝试，并按 `WORKER_RETRY_BASE_SECONDS * 2^(attempt-1)` 重新排队，上限为 `WORKER_RETRY_MAX_SECONDS`。每个任务在创建时快照 `WORKER_JOB_MAX_ATTEMPTS`；耗尽后统一进入 `failed`，并写入 `dead_lettered_at`、关闭助手占位消息和审计事件。授权撤销、取消和明确的永久错误不重试。

租约提供的是数据库写回 fencing，不等于外部系统副作用的 exactly-once。后续真实处理器必须使用稳定 `job.id` 作为下游幂等键或通过 transactional outbox 交付，不得使用每次变化的 lease token 作为业务幂等键。

## 数据同步

Scheduler 使用 `Asia/Shanghai` 的 `0 2 * * *` 默认计划，并通过 PostgreSQL advisory lock 与窗口幂等键避免重复创建任务。Scheduler 只排队；`ingestion-worker` 执行读取、校验、暂存和按域激活。

```bash
# 一次性注册或重新验证数据源
./scripts/configure-source.sh local-demo demo-enterprise "演示模拟数据"

# FDE 人工立即同步
./scripts/sync-now.sh local-demo demo-enterprise

# 查看同步、调度和模型服务日志
./scripts/logs.sh local-demo ingestion-worker
./scripts/logs.sh local-demo scheduler
./scripts/logs.sh local-demo hermes-runtime
```

商机、交付、回款和目标分别保留当前版本与最近成功时间。单域失败不会替换该域的上一成功版本；界面和回答必须展示该域的旧数据状态，不能把混合截止时间合并称为“最新”。

本机 Source PostgreSQL 是确定性演示源，可由 `rebuild-demo-source.sh` 重建；产品数据库与文件仍按正式备份链保护。客户外部脱敏源库的访问控制与备份由客户负责，产品备份不替代客户源库备份。

## Anspire 模型服务

Anspire 是产品唯一生成模型通道。企业管理员或 FDE 在管理端完成“保存配置 → 测试连接 → 启用”；不要把 API Key 写入 `.env`、Compose、命令行、工单、截图或日志。

演示前检查：

1. 管理端状态为“已启用”。
2. 最近一次连接测试成功，且选择的是版本内白名单模型。
3. 发起一条低风险测试问数，确认回答含数据时间、来源与证据下钻。
4. 日志中只有请求 ID、模型 ID、状态和时延，不含 API Key、提示词全文或模型原始响应。

常见状态：

- “未配置”：尚未保存企业凭证。
- “等待测试”：凭证或模型刚发生变化，必须重新测试。
- “测试失败”：凭证、模型权限、账户额度、网络出口或 Anspire 网关异常；系统保持停用。
- “待启用”：连接已通过，但尚未授权真实问答使用。
- “已启用”：真实问数与文件回答可以调用 Anspire。

凭证轮换采用先保存新 Key、再测试、最后启用的顺序。保存新 Key 后旧配置立即失效，系统不会同时保留两份可用明文凭证。若连接测试失败，先在管理端查看经过脱敏的错误，再检查 `api` 与 `hermes-runtime` 日志；禁止通过打印环境变量或数据库密文排障。

```bash
./scripts/logs.sh local-demo api
./scripts/logs.sh local-demo hermes-runtime
```

Hermes Runtime 只接收单次请求所需的短生命周期解密结果，不挂载持久模型密钥；API 与 Hermes 之间使用带时效与防重放请求标识的内部 HMAC 签名。出口策略只需放行 `open-gateway.anspire.ai:443`。

## 中文嵌入模型

首次启动会把固定版本的 `BAAI/bge-small-zh-v1.5` 安装到独立 Docker 卷，并校验发布时固定的 SHA-256。`file-worker` 只以离线模式读取该卷，不在解析文件时临时联网下载模型。

如果 `embedding-model-init` 失败：

1. 查看一次性初始化容器日志，不要绕过校验直接启动 `file-worker`。
2. 检查宿主机磁盘空间和到固定模型制品地址的 HTTPS 访问。
3. 重新执行 `./scripts/start.sh local-demo`；成功制品会复用，下载中断可续传。
4. 只有 `embedding-model-init` 成功退出且 smoke test 验证模型文件后，文件问答才算可用。

## 事故最低记录

- 环境、时间、发现人、影响范围
- 当前 revision 与镜像摘要
- 请求 ID、审计事件 ID、相关容器日志
- 是否涉及密钥、个人信息或经营数据
- 临时控制、根因、永久修复与验证
