# 生产化底座与第二阶段经营智能

本目录描述董事长 AI 秘书的可运行生产底座，以及第二阶段的脱敏经营数据、文件检索与 Hermes 受控问数链路。身份、权限、会话、审计和备份沿用第一阶段基线；经营数据只从标准脱敏源库进入，不直接连接客户原始 CRM。

## 已冻结的边界

- 线上 Sites Demo 保持原样，第二阶段生产工作只在 `codex/production-intelligence-phase2` 分支进行。
- 第一阶段只允许演示者从本机访问，网关仅监听 `127.0.0.1`。
- `local-demo` 与 `customer-template` 可同时保留和启动，但数据库、文件卷、密钥、端口、备份目录与 Compose project 完全分离。
- `customer-template` 永远不包含 Demo 数据、Demo 账号、默认口令或演示密钥。
- 第一阶段不制作离线安装包。离线交付在第五阶段完成后基于同一镜像与迁移契约制作。

## 第二阶段运行结构

```text
127.0.0.1:8080 / :8180
          │
       Nginx                     唯一宿主机监听；限流、安全头、同源 API
       ├── Web                   vinext 生产构建；不包含原型回退
       └── API ─────────────┐    身份、权限、业务 API、SSE 与证据；独立 API 数据库角色
                            ├── 产品 PostgreSQL + pgvector
      Assistant Worker ─────┤    回答、路由与证据；独立 assistant 数据库角色
           File Worker ─────┘    文件解析与当前会话检索；独立 file 数据库角色
             │
      Hermes 0.19.0 ─ MCP Hub   仅 Anspire；无 Shell/浏览器；带签名范围令牌调用 11 个只读工具
             │
      Ingestion Worker ← Scheduler（各自独立数据库角色；每日 02:00；单窗口幂等）
             │
      标准脱敏 Source PostgreSQL（独立数据库；产品只读）
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
- [第二阶段架构与运维](./phase2-intelligence.md)
- [第二阶段验收记录](./phase2-acceptance.md)
- [标准脱敏源库数据契约](./source-data-contract.md)

## 最短启动路径

```bash
./scripts/prepare-env.sh local-demo
./scripts/start.sh local-demo
./scripts/bootstrap-admin.sh local-demo admin@example.com "企业管理员" "演示企业" demo-enterprise
./scripts/create-executive.sh local-demo demo-enterprise chairman@example.com "董事长" enterprise
./scripts/seed-demo.sh local-demo demo-enterprise "SEED local-demo/demo-enterprise"
./scripts/rebuild-demo-source.sh local-demo demo-enterprise "REBUILD local-demo/demo-enterprise"
./scripts/configure-source.sh local-demo demo-enterprise "演示模拟数据"
./scripts/sync-now.sh local-demo demo-enterprise
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
