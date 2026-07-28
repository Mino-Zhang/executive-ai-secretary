# 飞书经营三表固定数据通道

## 运行边界

- 仅在本机 `local-demo` 启用飞书直连。
- 运行期使用企业自建应用的应用身份和只读权限。
- 不使用个人 OAuth Token，不向运行容器挂载写权限。
- 客户环境默认连接客户的标准脱敏 PostgreSQL，不默认启用飞书。

## 固定资源

| 数据域 | App Token | Table ID |
|---|---|---|
| 商机 | `DZ4ubC8sbaIfWts013icRGKznwc` | `tbl7g8Jxf697jdRN` |
| 项目交付 | `S8gWbRqsRavTbTs5Yu1c1XoPnN3` | `tbl755uvHTQcWL46` |
| 财务回款 | `HMhib2G0aaXwEnskNMEctZkinTe` | `tbl5WCtPleUeNo9k` |

文件夹 Token：`CvgMfe6FllrnkldMcaBckdIYnKT`

通道不根据表名搜索，而是使用 App Token、Table ID 和 Field ID 三层固定契约。字段被删除或更换类型时，同步会拒绝激活新版本；仅修改展示名称不会中断通道，运行时仍以稳定 Field ID 读取。

## 权限

飞书开放平台必须为应用 `cli_a9143e6a6d789bc4` 开通并发布应用身份权限：

- `bitable:app:readonly`

仅开通用户身份权限不足以支持后台定时任务。新增权限后还需要发布应用版本，并在企业管理后台完成审批。

## 同步语义

1. 分页读取三张表及字段元数据。
2. 检查 Field ID、字段名称、类型和必填值。
3. 检查商机—项目—回款关系和财务恒等式。
4. 高、中、低靠谱度分别使用 20%、10%、5% 保守权重。
5. 三表全部通过后，在脱敏源库中以一个事务发布同一批次。
6. 产品业务库在一个事务中同时切换商机、项目和回款。
7. 任一环节失败时保留上一成功版本，并将三个数据域标记为旧数据可用。

默认调度：每天 `02:00 Asia/Shanghai`。

## ODS 3.0 升级

已运行的本机或受管 Source PostgreSQL 不需要重建容器。使用非破坏性
[影子升级手册](./source-v3-shadow-upgrade.md)创建 `executive_source_v3`，完成校验后再由业务库做原子切换。

## 本机密钥导入

```bash
./scripts/configure-feishu-live.sh local-demo
```

该命令只从 macOS Keychain 读取应用密钥，并以 `0600` 权限写入本机运行时 Docker secret。

## 手工验证

```bash
docker exec executive-ai-local-demo-ingestion-worker-1 /bin/sh -ec \
  'export FEISHU_RUNTIME_SECRET="$(cat /run/secrets/feishu_runtime_secret)"; \
   python -m executive_ai_api.feishu_live validate'
```

成功输出必须同时包含：

- `valid: true`
- 商机 100 条
- 项目 18 条
- 回款 54 条
- 一个稳定的 `content_sha256`
