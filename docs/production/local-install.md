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

## 4. 配置唯一模型通道 Anspire

使用刚创建的企业管理员账号登录本机管理端。首次登录需先完成强制改密，然后在“模型服务”按顺序操作：

1. 从版本内白名单选择 Anspire 模型。
2. 将企业自己的 Anspire API Key 填入密钥框并保存。
3. 点击“测试连接”，确认官方网关、凭证和所选模型均可用。
4. 只有测试成功后，点击“启用 Anspire”。

正式网关固定为 `https://open-gateway.anspire.ai/v6`，管理端不能改成其他 OpenAI 兼容地址。后端纳入 Anspire 全量 53 个模型目录，管理端仅允许 39 个聊天与推理模型进入经营回答，GLM-5.2 为默认模型；图像、视频、Embedding 与 Rerank 模型保留在后端能力目录但不可误选。API Key 使用企业隔离的 AES-GCM 密钥加密保存；明文不会写入 `.env`、Docker Compose、浏览器存储、日志、回答证据或 Git。保存后页面只显示末四位掩码。

更换 API Key 或模型会自动停用生成服务，并要求重新测试。未配置、未测试或未启用时，经营问数会明确失败，不会降级到其他供应商或生成伪答案。

## 5. 创建可登录的董事长账号

首位企业管理员建立企业后，再创建董事长账号。最后一个参数可为 `enterprise`（企业全域）或逗号分隔的已配置事业部代码；口令同样从隐藏 stdin 读取并强制首登改密。

```bash
./scripts/create-executive.sh local-demo demo-enterprise chairman@example.com "董事长" enterprise
./scripts/create-executive.sh customer-template customer chairman@customer.example "董事长" east-china,key-projects
```

事业部代码必须已经由管理端建立；脚本不会为绕过授权而临时造事业部。CLI 拒绝重复邮箱，并写入 `cli.user_created` 审计事件。

## 6. 写入第二阶段脱敏演示数据

仅 `local-demo` 可执行，且必须提供精确确认短语：

```bash
./scripts/seed-demo.sh local-demo demo-enterprise "SEED local-demo/demo-enterprise"
```

Seed 必须指向已经由管理员初始化、且已有 executive 的企业；它幂等写入脱敏事业部、项目、会话与简报样本，不读取或创建任何凭据。`customer-template` 在 Compose、脚本与应用三层均拒绝此操作。

随后以固定版本、企业 ID 和参考日期生成标准经营数据，并通过与客户环境相同的同步链进入产品库：

```bash
./scripts/rebuild-demo-source.sh local-demo demo-enterprise "REBUILD local-demo/demo-enterprise"
./scripts/configure-source.sh local-demo demo-enterprise "演示模拟数据"
./scripts/sync-now.sh local-demo demo-enterprise
```

重建命令生成 6 个事业部、45 名负责人、600 个客户、3000 条商机、800 个项目、12000 条回款记录和 600 条目标。相同 `DEMO_DATASET_VERSION`、企业 ID 与 `DEMO_REFERENCE_DATE` 得到相同内容哈希。它只允许在 `local-demo` 执行，不会自动修改参考日期。

若要演示飞书多维表格同步，先在 `.env` 填入 App ID、App Token、Table ID，把一次性写凭证放入 `feishu_provisioning_secret`，再执行：

```bash
./scripts/publish-feishu-demo.sh local-demo demo-enterprise
```

运行期只读凭证必须单独写入 `feishu_runtime_secret`。客户环境默认不配置任何飞书凭证。

## 7. 验收

```bash
./scripts/status.sh local-demo
./scripts/smoke-test.sh local-demo
./scripts/status.sh customer-template
./scripts/smoke-test.sh customer-template
```

验收至少包括：

1. `nginx`、`web`、`api`、`worker`、`ingestion-worker`、`file-worker`、`scheduler`、`mcp-hub`、`hermes-runtime` 与 `postgres` 健康；角色初始化、迁移和权限重放容器成功退出。
2. `lsof` 显示端口只监听 `127.0.0.1`。
3. 未登录业务 API 返回 `401`，不是固定 Demo 内容。
4. 两套环境的项目名、端口、数据库名和卷名不同。
5. 重启容器后数据仍存在。
6. 企业管理员可以看到 Anspire 管理面；董事长账号无法访问模型配置 API。
7. Anspire 连接测试成功后才允许启用；更换模型或密钥后必须重新测试。
8. 文件 Worker 已加载经过 SHA-256 校验的中文嵌入模型，且运行期为离线加载。

## 常用命令

```bash
make ENV=local-demo status
make ENV=local-demo logs
make ENV=local-demo backup
make ENV=local-demo down
```

`down` 不删除卷；禁止使用 `down -v` 处理有价值的数据。
