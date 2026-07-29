# 日常运维手册

## 每次演示前

```bash
git status --short --branch
./scripts/start.sh local-demo
./scripts/status.sh local-demo
./scripts/smoke-test.sh local-demo
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

## 事故最低记录

- 环境、时间、发现人、影响范围
- 当前 revision 与镜像摘要
- 请求 ID、审计事件 ID、相关容器日志
- 是否涉及密钥、个人信息或经营数据
- 临时控制、根因、永久修复与验证
