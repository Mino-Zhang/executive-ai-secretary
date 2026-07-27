# 标准脱敏源库数据契约 2.0

第二阶段只接入 PostgreSQL 15 至 17 的固定 ODS 结构。正式 DDL 位于 `deploy/source-postgres/standard-ods.sql`，该文件是机器可执行的唯一事实来源；本文解释交付边界和字段含义。

## 安全边界

- 客户从 CRM、项目和财务系统抽取并完成脱敏，产品永远不连接原始 CRM。
- 客户删除电话、邮箱、身份证、地址、银行账号等问数不需要的字段，并提供稳定的脱敏业务 ID 与认可的展示名称。
- 产品连接账号必须是非超级用户、无建库/建角色/复制权限，并处于只读事务模式。
- 外部连接必须启用 TLS，连接串必须使用 `sslmode=verify-full`。
- 产品只执行本文白名单列的显式 `SELECT`，从不执行 `SELECT *`；客户新增字段不会被读取。
- 产品不保存真实 ID 与脱敏 ID 的反查关系。

## 公共字段

除 `ods_schema_version` 和 `source_batches` 外，每张 ODS 表必须包含：

| 字段 | 类型 | 规则 |
|---|---|---|
| `source_system` | `varchar(80)` | 客户认可的来源标识 |
| `source_record_id` | `varchar(160)` | 来源内稳定且已脱敏的业务 ID |
| `source_updated_at` | `timestamptz` | 源记录最近更新时间 |
| `load_batch_id` | `varchar(160)` | 关联成功或准备中的 `source_batches.batch_id` |
| `is_deleted` | `boolean` | 软删除标志；删除时保留稳定 ID |
| `organization_code` | `varchar(80)` | 关联 `ods_organization_unit.organization_code` |

`source_system + source_record_id` 在每张表内唯一。所有时间写入带时区值，所有金额使用人民币元与 `numeric(18,2)`，概率使用 0 至 100 的整数。

## 控制表

### `ods_schema_version`

单行记录，`singleton=true`，`schema_version` 必须为 `2.0`。版本不匹配时产品拒绝同步。

### `source_batches`

| 必填字段 | 用途 |
|---|---|
| `batch_id` | 全局稳定的导入批次标识 |
| `source_system` | 本批次来源 |
| `dataset_version` | 数据集或客户 ETL 版本 |
| `reference_date` | 经营参考日期 |
| `source_data_as_of` | 本批次数据截止时间 |
| `status` | `preparing`、`ready` 或 `failed`；产品只读取 `ready` |
| `record_counts` | 各域记录数 JSON |
| `content_sha256` | 可重复对账的数据内容哈希 |
| `validation_result` | 客户侧校验结果 JSON |

客户应先以 `preparing` 写批次和 ODS 行，完成内部校验后在同一事务中切换为 `ready`。失败批次不得标为 `ready`。

## 业务表白名单

| 表 | 业务字段（公共字段之外） |
|---|---|
| `ods_organization_unit` | `organization_code`, `parent_organization_code`, `display_name`, `unit_type`, `sort_order` |
| `ods_person` | `display_name`, `role_title`, `is_active` |
| `ods_customer` | `owner_person_record_id`, `display_name`, `industry`, `region`, `customer_since` |
| `ods_opportunity` | `customer_record_id`, `owner_person_record_id`, `opportunity_code`, `title`, `stage`, `status`, `probability`, `expected_amount`, `expected_gross_profit`, `created_date`, `expected_close_date`, `closed_date` |
| `ods_delivery` | `opportunity_record_id`, `customer_record_id`, `manager_person_record_id`, `project_code`, `project_name`, `status`, `risk_level`, `completion_percent`, `contract_amount`, `gross_margin_rate`, `planned_start_date`, `planned_end_date`, `actual_end_date`, `current_milestone`, `delay_days` |
| `ods_collection` | `project_record_id`, `customer_record_id`, `invoice_amount`, `receivable_amount`, `collected_amount`, `planned_collection_date`, `actual_collection_date`, `overdue_days`, `aging_bucket`, `status` |
| `ods_target` | `metric_code`, `metric_name`, `period_type`, `period_start`, `period_end`, `target_value`, `unit` |

## 关系与恒等式

- 人员、客户、商机、交付、回款和目标的事业部必须存在且编码稳定。
- 商机关联的客户和负责人必须存在。
- 交付必须关联赢单商机、客户和项目负责人。
- 回款必须关联交付项目与同一客户、事业部。
- `0 <= collected_amount <= receivable_amount <= invoice_amount`。
- `expected_gross_profit <= expected_amount`，`0 <= gross_margin_rate <= 1`。
- 逾期天数由计划回款日和数据参考日期计算；未逾期记录不得伪造正逾期天数。
- 目标必须关联事业部、指标与完整周期，`period_end >= period_start`。

## 客户交付方式

### 连接客户已有脱敏 PostgreSQL

1. 客户 DBA 执行标准 DDL。
2. 客户 ETL 用自有写账号写入脱敏数据。
3. 客户创建只读账号，只授予 `executive_source` Schema 的 `USAGE` 与白名单表 `SELECT`。
4. 把只读 URL 写入 `runtime/customer-template/secrets/source_database_url`，URL 必须含 `sslmode=verify-full`。
5. 执行 `configure-source.sh`；返回缺失表、缺失列、版本、账号权限和 TLS 结果。

### 使用可选托管 Source 容器

在客户 `.env` 设置 `MANAGED_SOURCE_DB=true` 后启动。客户 ETL 通过仅绑定 `127.0.0.1:${MANAGED_SOURCE_HOST_PORT}` 的 `source_writer` 写入；产品仍只使用 `source_reader`。Source 卷与产品数据库卷分离，客户仍负责其备份与保留策略。

## 变更规则

二阶段不自动适配任意客户表。新增可选字段必须先升级契约版本、DDL、白名单读取代码、数据字典和兼容测试；删除或改变现有字段属于不兼容变更。实施人员不得用高权限账号或临时 `SELECT *` 绕过校验。
