# 双环境、安全与密钥

## 环境矩阵

| 项目 | `local-demo` | `customer-template` |
|---|---|---|
| 用途 | 演示者本机脱敏演示 | 新客户空白起点 |
| 应用模式 | `demo` | `production` |
| 网关 | `127.0.0.1:8080` | `127.0.0.1:8180` |
| Compose project | `executive-ai-local-demo` | `executive-ai-customer-template` |
| 数据库 | `executive_ai_demo` | `executive_ai_customer` |
| Demo Seed | 仅显式确认后允许 | 三层拒绝 |
| 默认账号/口令 | 无 | 无 |
| 数据与文件卷 | 独立 | 独立、空白 |
| 密钥和备份 | 独立 | 独立 |

## 网络不变量

- `nginx` 是唯一映射宿主机端口的服务。
- 网关覆盖客户端传入的 `X-Forwarded-For`，只把实际 TCP 对端地址交给 API；否则审计来源与登录限流可被伪造。
- `HOST_BIND` 在第一阶段必须等于 `127.0.0.1`，脚本遇到其他值立即退出。
- Postgres 位于 Compose internal network，没有宿主机端口。
- 只有 API 与 Worker 访问私有文件卷；浏览器、Web、迁移和数据库工具容器不直接访问文件字节。
- 用户已确认客户不会访问本机演示环境，由演示者现场操作，因此不提供临时公网隧道或局域网暴露。

## 密钥

`prepare-env.sh` 生成：

- PostgreSQL bootstrap owner、非超级用户 migrator、应用 runtime、只读 backup 四个独立随机口令
- Session 签名密钥
- CSRF 密钥
- 32 字节 AES 文件加密密钥
- 初始为空的文件历史密钥 ring（JSON，供受控轮换保留旧版本）
- 独立审计 HMAC 密钥，用于验证审计事件完整性
- 初始为空的审计历史密钥 ring（JSON，保证轮换后仍可验证旧事件）
- 备份加密密钥
- Ed25519 备份清单签名密钥与对应公钥

密钥通过只读 Docker secrets 挂载，并在容器启动脚本中注入进程环境。审计 HMAC、Session、CSRF、文件与备份密钥彼此独立，不允许复用。密钥内容不会出现在 `docker compose config`、镜像层或 GitHub Actions 配置中。

## 数据库角色边界

| 角色 | 用途 | 明确禁止 |
|---|---|---|
| owner | 首次角色初始化、灾难恢复 | 不挂载到 API、Worker、Seed、Bootstrap |
| migrator | Alembic 与 `public` 应用对象所有权 | 非 superuser，无建库/建角色/绕过 RLS |
| runtime | API、Worker、Seed、账号初始化的日常 DML | 非 superuser；不能修改/删除审计事件，不能改迁移版本 |
| backup | `pg_dump` 与备份清单只读查询 | 非 superuser；无 INSERT/UPDATE/DELETE/DDL |

每次启动先以 owner 幂等校准三个受限角色，再由 migrator 执行迁移，最后重放现有对象和默认权限。`audit_events` 对 runtime 仅允许 SELECT/INSERT；`audit_chain_heads` 允许链头所需 SELECT/INSERT/UPDATE，但禁止 DELETE/TRUNCATE。恢复后同样重放所有权与权限。

服务密钥也按职责收敛：Worker 不挂载 Session/CSRF，Migrator 不挂载任何应用密钥，Bootstrap/Seed 只挂载 runtime 数据库口令与审计密钥，备份工具只挂载 backup 数据库口令。

账号初始化也不包含固定口令：`bootstrap-admin.sh` 建立首位企业管理员，`create-executive.sh` 建立可登录的董事长账号。两者都从隐藏 stdin 读取一次性密码并强制首登改密；董事长的数据范围必须明确选择企业全域或已存在的事业部代码。

生产事故处理：

1. 先阻断访问并保留日志。
2. 对数据库、Session 与 CSRF 密钥执行受控轮换。
3. 文件密钥和备份密钥不能直接覆盖；必须先完成数据重加密迁移并验证恢复。
4. 记录轮换原因、操作者、时间、受影响环境和验收结果。

## 网关保护

- 登录接口 5 次/分钟/IP，突发 3 次。
- 一般 API 20 次/秒/IP，突发 40 次。
- 单客户端并发连接限制为 30。
- 上传请求上限约 52 MB，对应产品单文件 50 MB 约束与协议开销。
- 安全头禁止 iframe、对象嵌入、摄像头、定位和支付能力；CSP 仅允许同源资源。
- API 与前端保持同源，避免浏览器直接持有后端地址或跨域凭据。

限流只是网关第一层。身份 API 仍需账号级失败计数、Session 撤销、CSRF、授权与审计。

## 客户模板零污染检查

每次交付前执行：

```bash
./scripts/test-infra.sh
./scripts/compose.sh customer-template config | less
```

并人工确认：数据库为空、文件卷为空、无账号、`SEED_DEMO_DATA=false`、无 Demo 标识、无演示密钥、备份目录为空。
