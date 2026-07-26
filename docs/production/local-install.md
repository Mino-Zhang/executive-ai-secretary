# 本机安装与首次启动

## 前置条件

- macOS Apple Silicon 或 Linux `amd64/arm64`
- Docker Desktop 29+，Docker Compose v2/v5
- Git、OpenSSL、curl
- 建议至少 16 GB 内存、20 GB 可用磁盘；演示数据与备份增长后应单独评估容量

检查：

```bash
docker version
docker compose version
openssl version
```

## 1. 准备环境

首次运行会生成非密钥 `.env`、数据库 owner/migrator/runtime/backup 四个独立口令、Session/CSRF/文件/审计/备份密钥，以及一对 Ed25519 备份签名密钥；私钥与口令权限均为 `0600`。命令不会创建任何默认用户或默认口令。

```bash
./scripts/prepare-env.sh local-demo
./scripts/prepare-env.sh customer-template
```

生成目录：

```text
runtime/
├── local-demo/
│   ├── .env
│   └── secrets/
└── customer-template/
    ├── .env
    └── secrets/
```

不要复制一个环境的密钥给另一个环境。`prepare-env.sh` 永远不会覆盖现有密钥；密钥轮换必须使用独立的备份、数据库口令切换和文件/备份重加密流程。

若环境是在数据库角色拆分或版本化密钥环之前创建，先执行以下幂等升级。它只生成缺失的 migrator/runtime/backup 三个角色口令和两个空密钥环文件，绝不读取、重写或轮换任何既有密钥：

```bash
./scripts/upgrade-env-secrets.sh local-demo
./scripts/upgrade-env-secrets.sh customer-template
```

## 2. 启动

```bash
./scripts/start.sh local-demo
./scripts/start.sh customer-template
```

- Demo：<http://127.0.0.1:8080>
- 客户空模板：<http://127.0.0.1:8180>

两套环境的 Postgres 与文件卷不会暴露到宿主机端口，也不会共享网络或卷。

## 3. 创建首位管理员

系统交互读取一次性密码，密码不出现在命令历史、Compose 文件或环境模板中。账号首次登录后必须改密。

```bash
./scripts/bootstrap-admin.sh local-demo admin@example.com "企业管理员" "演示企业" demo-enterprise
./scripts/bootstrap-admin.sh customer-template admin@customer.example "企业管理员" "客户企业" customer
```

生产模板不允许通过 Seed 创建账号。

## 4. 创建可登录的董事长账号

首位企业管理员建立企业后，再创建董事长账号。最后一个参数可为 `enterprise`（企业全域）或逗号分隔的已配置事业部代码；口令同样从隐藏 stdin 读取并强制首登改密。

```bash
./scripts/create-executive.sh local-demo demo-enterprise chairman@example.com "董事长" enterprise
./scripts/create-executive.sh customer-template customer chairman@customer.example "董事长" east-china,key-projects
```

事业部代码必须已经由管理端建立；脚本不会为绕过授权而临时造事业部。CLI 拒绝重复邮箱，并写入 `cli.user_created` 审计事件。

## 5. 可选：写入脱敏 Demo 数据

仅 `local-demo` 可执行，且必须提供精确确认短语：

```bash
./scripts/seed-demo.sh local-demo demo-enterprise "SEED local-demo/demo-enterprise"
```

Seed 必须指向已经由管理员初始化、且已有 executive 的企业；它幂等写入脱敏事业部、项目、会话与简报样本，不读取或创建任何凭据。`customer-template` 在 Compose、脚本与应用三层均拒绝此操作。

## 6. 验收

```bash
./scripts/status.sh local-demo
./scripts/smoke-test.sh local-demo
./scripts/status.sh customer-template
./scripts/smoke-test.sh customer-template
```

验收至少包括：

1. `nginx`、`web`、`api`、`worker`、`postgres` 健康；角色初始化、迁移和权限重放容器成功退出。
2. `lsof` 显示端口只监听 `127.0.0.1`。
3. 未登录业务 API 返回 `401`，不是固定 Demo 内容。
4. 两套环境的项目名、端口、数据库名和卷名不同。
5. 重启容器后数据仍存在。

## 常用命令

```bash
make ENV=local-demo status
make ENV=local-demo logs
make ENV=local-demo backup
make ENV=local-demo down
```

`down` 不删除卷；禁止使用 `down -v` 处理有价值的数据。
