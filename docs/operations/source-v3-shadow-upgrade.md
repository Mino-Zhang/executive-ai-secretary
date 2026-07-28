# 脱敏源库 ODS 3.0 影子升级

## 用途

`scripts/upgrade-source-v3.sh` 用于将已经运行的本机 `local-demo` 或启用了
`MANAGED_SOURCE_DB=true` 的 `customer-template` 源库升级到 ODS 3.0 影子契约。

该操作不会：

- 重建或重启容器。
- 删除 `executive_source` V2 Schema。
- 删除或改写 V2 数据。
- 切换业务库当前批次。
- 触发飞书同步。

它只会在同一个 Source PostgreSQL 中新建或校准独立的
`executive_source_v3` Schema，并重新收紧 `source_reader` 和 `source_writer` 在该
Schema 上的权限。

## 执行前检查

脚本会自动拒绝以下情况：

- 未知环境名。
- `customer-template` 未启用受管源库。
- `source-postgres` 未运行或不属于目标 Compose 项目。
- PostgreSQL 不在 15–17 版本范围。
- V2 Schema 不存在或版本不是 `2.0`。
- `source_reader` / `source_writer` 缺失或拥有高权限角色属性。

先做只读预检：

```bash
./scripts/upgrade-source-v3.sh local-demo --check
```

对受管客户源库预检：

```bash
./scripts/upgrade-source-v3.sh customer-template --check
```

## 执行升级

本机演示环境：

```bash
./scripts/upgrade-source-v3.sh local-demo
```

受管客户源库：

```bash
./scripts/upgrade-source-v3.sh customer-template
```

脚本会记录当前 `standard-ods-v3.sql` 的 SHA-256，先将契约标记为
`3.0-validating`，执行幂等 DDL，再校验全部白名单列的类型和非空性、主键、
唯一约束、外键、查询索引和三张快照表的不可变触发器。只有目录校验完整通过，
才会在同一 DDL 事务末尾写入正式版本 `3.0`。

完成后脚本回读：

- V2 版本仍为 `2.0`。
- V3 版本为 `3.0`。
- V3 基础表数量为 8。
- `source_reader` 只能 `SELECT`。
- `source_writer` 可插入快照，但不能 `UPDATE` 或 `DELETE` 三张 ODS 快照表。

若现有同名表存在类型、约束或触发器漂移，升级会直接失败并保留
`3.0-validating`；产品运行时也会执行同级别的结构校验并拒绝读取。

## 权限边界

`source_reader` 只读取以下白名单对象：

- Schema 版本和批次元数据。
- 飞书三表字段绑定、校验问题和同步检查点。
- 商机、项目交付和财务回款快照。

`source_writer` 可以：

- 创建批次并转换批次状态。
- 插入字段绑定、校验问题和三张 ODS 快照。
- 更新同步检查点。

`source_writer` 不能更新或删除已写入的 ODS 快照；数据库触发器会对更高权限账号的误操作再做一层拒绝。

## 后续切换

V3 Schema 就绪不等于业务数据已切换。正式切换仍需依次完成：

1. 飞书三表全量读取。
2. 字段、主键、跨表关系和金额恒等式校验。
3. 将一个完整批次写入 `executive_source_v3`。
4. 业务库三域在单一事务内原子激活。

三表任意一项未通过时，不执行第 4 步。
