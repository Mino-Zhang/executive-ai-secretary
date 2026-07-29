# 生产化第一阶段：本机生产底座

本目录描述董事长 AI 秘书第一阶段的可运行生产底座。当前目标不是把所有业务能力一次接完，而是让身份、权限、数据、会话、文件、审计、备份和交付方式从 Demo 进入可信状态。

## 已冻结的边界

- 线上 Sites Demo 保持原样，生产工作只在 `codex/production-foundation` 分支进行。
- 第一阶段只允许演示者从本机访问，网关仅监听 `127.0.0.1`。
- `local-demo` 与 `customer-template` 可同时保留和启动，但数据库、文件卷、密钥、端口、备份目录与 Compose project 完全分离。
- `customer-template` 永远不包含 Demo 数据、Demo 账号、默认口令或演示密钥。
- 第一阶段不制作离线安装包。离线交付在第五阶段完成后基于同一镜像与迁移契约制作。

## 运行结构

```text
127.0.0.1:8080 / :8180
          │
       Nginx                     唯一宿主机监听；限流、安全头、同源 API
       ├── Web                   vinext 生产构建；不包含原型回退
       └── API ─────────────┐    身份、权限、业务 API、健康检查；runtime 角色
                            ├── PostgreSQL（owner / migrator / runtime / backup）
          Worker ───────────┘    异步任务；runtime 角色，不持有 Session/CSRF
             │
         私有文件卷               API/Worker 可见，Web/Nginx 不可见
```

所有容器日志输出到标准输出，使用 JSON 日志驱动轮转；Nginx 访问日志自身也是 JSON。业务密钥保存在 `runtime/<environment>/secrets/`，不进入 Git、镜像、环境模板或日志。

文件加密与审计 HMAC 的版本化、历史验签和受控轮换见 [key-rotation.md](./key-rotation.md)。

## 文档导航

- [本机安装与首次启动](./local-install.md)
- [双环境、安全与密钥](./environments-and-security.md)
- [备份、校验与恢复](./backup-and-restore.md)
- [日常运维手册](./operations-runbook.md)
- [客户快速部署准备](./customer-deployment.md)
- [CI/CD 与发布](./ci-cd.md)

## 最短启动路径

```bash
./scripts/prepare-env.sh local-demo
./scripts/start.sh local-demo
./scripts/bootstrap-admin.sh local-demo admin@example.com "企业管理员" "演示企业" demo-enterprise
./scripts/create-executive.sh local-demo demo-enterprise chairman@example.com "董事长" enterprise
./scripts/seed-demo.sh local-demo demo-enterprise "SEED local-demo/demo-enterprise"
./scripts/smoke-test.sh local-demo
```

客户空模板使用独立命令：

```bash
./scripts/prepare-env.sh customer-template
./scripts/start.sh customer-template
./scripts/bootstrap-admin.sh customer-template admin@customer.example "企业管理员" "客户企业" customer
./scripts/create-executive.sh customer-template customer chairman@customer.example "董事长" enterprise
```

上述命令不会向公网或局域网开放端口。
