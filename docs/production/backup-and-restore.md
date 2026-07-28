# 备份、校验与恢复

## 备份内容

数据库导出由独立的只读 `backup` 角色执行；它不持有 DML、DDL 或超级用户权限。恢复仍由不暴露给应用的 bootstrap owner 执行，随后立即把应用对象所有权交还 migrator 并重放 runtime/backup ACL。

一次备份包含：

- 产品 PostgreSQL 自定义格式逻辑备份
- 若环境启用了托管脱敏源库，则包含独立的源库 PostgreSQL 自定义格式逻辑备份
- 私有文件卷归档
- 不含密钥的清单：环境、模式、项目名、数据库名、企业数量与 slug、Git revision、Alembic revision、时间与 SHA-256

备份脚本会短暂停止 API、数据导入 Worker、任务 Worker 与 Scheduler，再依次采集产品库、托管脱敏源库和私有文件卷，并写入 `application-quiesced` 一致性标记。这样在没有存储快照能力的本机部署中，三个制品共享同一个应用写入静默窗口；完成校验后原服务自动恢复。外部客户自管的脱敏源库不属于产品备份范围，仍由客户自身的数据库备份制度负责。

两个数据库和文件归档分别使用 AES-256-CBC + PBKDF2（200,000 次）加密。清单使用环境独立的 Ed25519 私钥签名，恢复前先用对应公钥验证，防止攻击者同时替换密文与校验值。备份密钥只存在于目标环境的运行时密钥目录；审计 HMAC 密钥随应用密钥受控备份，但不能与备份加密或签名密钥复用。

私有文件工具不使用 root 或额外 Linux capability。API、Worker 与文件工具固定使用同一数值 UID/GID `999:999`；因此工具可以读取和恢复权限为 `0700` 的加密存储树，同时仍保持无网络、`no-new-privileges` 与全部 capability 移除。

## 创建并验证

```bash
./scripts/backup.sh local-demo
./scripts/backup.sh customer-template
```

脚本在完成后自动执行校验：

1. 验证清单 Ed25519 签名，并核对目标环境。
2. 核对产品库、可选托管源库和文件归档的 SHA-256。
3. 解密每个数据库流并执行 `pg_restore --list`。
4. 解密文件流并执行 `tar -t`。

也可单独复核：

```bash
./scripts/verify-backup.sh local-demo /absolute/path/to/backup
```

仅有加密备份而没有对应 `backup_encryption_key` 无法恢复。生产备份应把密钥与数据备份存入不同受控介质。

## 恢复

恢复会替换数据库对象与私有文件卷，必须提供精确二次确认：

```bash
./scripts/restore.sh local-demo /absolute/path/to/backup "RESTORE local-demo"
```

恢复脚本会先完整验证所有清单制品，并用当前 API 镜像读取 Alembic 迁移图；只有备份 revision 是当前唯一 head 的已知祖先时才会继续。通过前置门禁后，脚本自动创建并记录 `pre-restore` 安全备份，再停止所有对外与写入服务，按以下顺序恢复：

1. 在单个事务中替换产品 PostgreSQL。
2. 把产品库对象所有权交给 migrator，前向迁移到当前 head，重放最小权限并复核 revision。
3. 若清单包含托管源库制品，在单个事务中替换源 PostgreSQL，再重放当前源数据契约与 `source_reader` / `source_writer` 最小权限。
4. 先把私有文件完整解压到同卷暂存目录；成功后才替换当前文件内容。
5. 统一启动 API、MCP、Hermes、Worker、Web 与网关并执行 smoke test。

安全备份不会自动删除。任一步失败时，脚本不会自动启动半恢复环境，而是保持应用服务停止，并输出此次 `pre-restore` 安全备份的绝对路径；失败阶段和安全备份路径同时写入仅当前用户可读的 `runtime/<environment>/restore.log`。操作者必须先检查失败位置，再使用该安全备份完成受控回滚。

早期备份清单可能没有 `source_database_file`。这类备份仍可恢复产品库与文件；若目标环境现在启用了托管源库，恢复脚本会明确记录 `retained-legacy` 并保留源库当前内容，不会把空库或推测数据覆盖进去。由于这不是三个制品的同点恢复，恢复后必须先验证数据源批次与业务库状态，再允许执行下一次同步。

### 强制保护

- `local-demo` 备份永远不能恢复到 `customer-template`。
- 跨环境恢复即使传入覆盖参数，也必须先走单独的数据迁移与重加密评审；通用脚本不直接执行。
- 清单不匹配、校验失败、解密失败、归档损坏、未知/未来 revision、迁移分叉或多 head 都会在修改数据前终止。
- 清单包含托管源库制品、但目标部署没有 `source-postgres` 服务时，恢复会在修改数据前拒绝执行。
- 恢复不会直接以旧 schema 启动 API；migrator 必须成功到达当前唯一 head，权限重放与 revision 复核通过后，API/Worker 才会重新启动。
- 进入破坏性恢复后发生错误时，应用保持停止；禁止绕过提示直接执行 `start.sh`。

## 恢复演练

第一阶段完成前必须至少做一次：

1. 创建测试会话与文件。
2. 备份并记录校验结果。
3. 新增一批可识别的临时数据。
4. 恢复备份。
5. 确认产品库与托管源库中的临时数据都消失、原数据恢复、会话权限正确、文件可解密读取。
6. 记录 RPO、RTO、操作者与异常。

未经恢复演练的备份不能视为可用备份。

CI 使用 `scripts/ci-recovery-drill.sh` 在全新的 GitHub Actions runner 中自动完成上述路径。脚本具有双重环境保护，只在 `CI=true` 且 `GITHUB_ACTIONS=true` 时运行，并拒绝任何已存在的运行时或备份目录；结束时只删除该临时 Compose project 的卷。它不会在演示者本机执行破坏性恢复。
