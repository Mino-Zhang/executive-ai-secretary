# 经营数据 3.0 切换手册

本手册只适用于已经启用 ODS 3.0 的 `local-demo` Production。独立冻结的 Demo
版本不在操作范围内。

## 安全边界

- 三张飞书表作为一个完整批次读取、校验和激活。
- 日常同步中的源库 ODS 事实只追加，不覆盖历史批次；切换期旧模拟批次的受控清理必须停掉 Worker、持有数据库锁并留下签名审计。
- 未获得 `valid=true` 的实时三表批次前，禁止执行模拟数据清理。
- 清理命令只允许 `APP_ENV=local-demo`，并且要求精确确认文本与已校验备份引用。
- 产品对脱敏源库使用只读账号；本机飞书导入器单独使用最小写入账号。

## 切换顺序

1. 生成并验证 Product 与 Source PostgreSQL 加密备份。
2. 对仍只有 V2 的历史源库，先只读预检并建立 V3 Schema：

   ```bash
   ./scripts/upgrade-source-v3.sh local-demo --check
   ./scripts/upgrade-source-v3.sh local-demo
   ```

   全新部署已经直接安装 ODS 3.0，不执行该历史迁移脚本。

3. 迁移业务库到 Alembic `72e1b4c8a903`。
4. 将 DataSource 切换为 `executive_source_v3` / `3.0`。
5. 执行“校验但不生效”，确认实时结果为 100 商机、18 项目、54 回款，金额为
   533.6 / 238.5 / 295.1 万元。
6. 执行受保护清理。先干跑：

   ```bash
   ./scripts/reset-local-demo-operating-data-v3.sh \
     local-demo --dry-run '<enterprise-slug>'
   ```

   仅在干跑清单与备份完全一致后执行：

   ```bash
   ./scripts/reset-local-demo-operating-data-v3.sh \
     local-demo \
     '<verified-backup-directory>' \
     'CLEAR local-demo operating-data-v3' \
     '<enterprise-slug>'
   ```

   包装脚本会先完整验证备份签名、Product/Source 加密库文件、
   文件卷与所属环境，再将已验签 Manifest 哈希传给一次性清理命令。
   同一企业完成一次切换清理后，命令会永久拒绝再次执行。

7. 执行“立即同步并原子切换”。失败时产品显示“经营数据尚未完成接入”，
   不回填旧模拟事实。
8. 核对三个数据域指向同一 `source_batch_id` 和 `active_sync_run_id`，再执行 MCP
   基准问数。

## 飞书应用身份

运行期使用 tenant/application access token，不使用个人 OAuth token。应用必须对当前
已发布版本开通至少一项：

- `bitable:app:readonly`
- `bitable:app`
- `base:field:read`

权限开通后还需发布应用版本并确认应用对三张表可见。个人 OAuth 能读取不等于
运行应用身份已经获权。
