# 文件加密与审计密钥轮换 Runbook

本流程只面向受控维护窗口。密钥正文只允许来自进程环境或权限为 `0600` 的 JSON 文件；数据库仅保存不可逆推出密钥的版本标识。任何密钥、ring 文件或完整文件内容都不得写入 Git、日志、审计 metadata 或数据库。

## 版本与文件格式

- 新文件使用 `EAIF2`：密文头包含密钥版本，数据库 `files.encryption_key_version` 同时保存版本。AES-256-GCM 的 AAD 绑定存储路径与密钥版本。
- 既有 `EAIF1` 文件按数据库版本读取，首次轮换后升级到 `EAIF2`。
- `FILE_ENCRYPTION_KEY_VERSION` 指向当前写入版本；`FILE_ENCRYPTION_KEY` 是该版本的 32 字节 URL-safe Base64 密钥。
- `FILE_ENCRYPTION_KEY_RING` 或 `FILE_ENCRYPTION_KEY_RING_FILE` 提供历史版本，格式为 `{"v1":"base64..."}`。二者不能同时设置。
- 审计对应使用 `AUDIT_HMAC_KEY_VERSION`、`AUDIT_HMAC_KEY`、`AUDIT_HMAC_KEY_RING[_FILE]`。`AUDIT_HMAC_LEGACY_KEY_VERSION` 指定升级前无版本字段事件所使用的版本，默认 `v1`。

文件 ring 与审计 ring 必须分开，当前密钥可以同时出现在 ring 中，但相同版本的值必须完全一致。单个 ring 最多 32 个版本。

## 文件密钥轮换

### 1. 准备与备份

1. 确认当前系统无正在执行的文件上传、下载或解析任务。
2. 创建并自动校验一致性备份：

   ```bash
   ./scripts/backup.sh local-demo key-rotation-v2
   ```

3. 记录脚本输出的绝对备份目录。轮换 CLI 会再次验证 Ed25519 清单签名、环境、时间、Alembic revision 及数据库/文件密文 SHA-256；缺少合格备份时拒绝执行。
4. 生成新文件密钥和 `0600` ring 文件。ring 至少包含旧版本；当前新密钥单独保存。不要在命令历史中直接写密钥正文。
5. 停止写入服务并保持 PostgreSQL 运行：

   ```bash
   ./scripts/compose.sh local-demo stop api worker nginx
   ```

### 2. 预检

在维护容器中挂载新密钥与 ring，先执行 `--dry-run`。下面的路径均须替换成绝对路径：

```bash
./scripts/compose.sh local-demo run --rm \
  -v /absolute/rotation/file-key-v2:/run/rotation/current-file-key:ro \
  -v /absolute/rotation/file-ring.json:/run/rotation/file-ring.json:ro \
  -e FILE_ENCRYPTION_KEY_VERSION=v2 \
  -e FILE_ENCRYPTION_KEY_RING_FILE=/run/rotation/file-ring.json \
  api /bin/sh -ec '
    DB_PASSWORD="$(cat /run/secrets/postgres_runtime_password)"
    export DATABASE_URL="postgresql+psycopg://${POSTGRES_RUNTIME_USER}:${DB_PASSWORD}@postgres:5432/${POSTGRES_DB}"
    export FILE_ENCRYPTION_KEY="$(cat /run/rotation/current-file-key)"
    export AUDIT_HMAC_KEY="$(cat /run/secrets/audit_hmac_key)"
    python -m executive_ai_api.rotate_file_keys --from-version v1 --to-version v2 --dry-run
  '
```

预检会逐个解密并核对文件大小与 SHA-256，但不写文件或数据库。

### 3. 执行与恢复

使用同一容器挂载，同时挂载备份目录与环境的备份签名公钥，再执行：

```bash
python -m executive_ai_api.rotate_file_keys \
  --from-version v1 \
  --to-version v2 \
  --backup-dir /backup \
  --backup-public-key /run/rotation/backup-signing-public-key \
  --batch-size 25 \
  --confirm 'ROTATE FILE KEYS v1 TO v2'
```

每个文件遵循“校验旧密文 → 写入并 `fsync` 临时密文 → 原子替换 → 用新密钥复验 → 更新数据库版本 → 写 FileEvent 与审计事件 → 单文件提交”。中断后重复完全相同的命令即可恢复：若密文已经是新版本而数据库仍是旧版本，CLI 会复验后只对账数据库，不会再次重写。PostgreSQL advisory lock 会拒绝并行轮换。

可用 `--max-files N` 做分批维护；输出中的 `remaining` 必须归零才能视为完成。完成后再次执行：

```bash
python -m executive_ai_api.rotate_file_keys \
  --from-version v1 --to-version v2 --verify-only
```

### 4. 切换运行密钥

1. 保留旧密钥在只读 ring 中，将运行时 `file_encryption_key` 安全替换为新密钥，并配置 `FILE_ENCRYPTION_KEY_VERSION=v2` 与 ring 文件。
2. 依次启动 API/Worker；就绪探针会完成当前密钥加密往返自检。
3. 上传、下载一个可识别测试文件，并核对审计事件。
4. 旧密钥在以下条件全部满足前不得删除：数据库无旧版本活动文件、所有保留期内备份均已过期或完成重加密、至少一次恢复演练成功、变更单获得批准。

失败时不要手工修改 `files.encryption_key_version`。停止服务，保留 ring，重跑 CLI；若无法恢复，则使用轮换前备份按正式恢复流程整体回退。

## 审计 HMAC 密钥轮换

审计事件不会重新签名。新事件记录 `audit_key_version`，链头记录 `anchor_key_version`；验签按各自版本从 ring 取密钥。链从旧密钥切换到新密钥时，会先用旧版本验证当前链头，再用新版本签署新事件与新链头，因此旧事件保持原始证据价值。

步骤：

1. 创建并验证备份，停止 API、Worker 和管理写入。
2. 生成新审计 HMAC 密钥，保留旧版本在审计 ring；确认 `AUDIT_HMAC_LEGACY_KEY_VERSION` 仍指向升级前事件密钥。
3. 设置新的 `AUDIT_HMAC_KEY_VERSION` 与 `AUDIT_HMAC_KEY`，启动迁移/应用。
4. 通过管理员审计完整性接口验证全部企业链，再产生一条受控测试事件并再次验证。
5. 审计旧密钥至少保留到所有审计事件与备份的法定保留期结束。丢失任何仍被事件引用的版本都会使历史验签失败；系统会失败关闭，不会退回当前密钥猜测验证。

密钥轮换不能代替密钥托管。客户正式部署应将当前密钥和历史 ring 存入客户认可的 Secret Manager/HSM，并对读取、变更、导出和销毁分别授权与审计。
