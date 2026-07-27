# 第二阶段经营智能：架构与操作

## 数据链

```text
本机：确定性生成器 → Source PostgreSQL ← 飞书模拟 SA 商机
客户：原始系统 → 客户自行脱敏 → Source PostgreSQL
                                  ↓ 只读白名单
Scheduler → data.sync Job → Ingestion Worker → 产品事实表/每日快照
                                                    ↓
用户范围 → HMAC 能力令牌 → MCP Hub 11 个只读工具 → Hermes 0.19.0
当前会话文件 → File Worker → pgvector/关键词混合检索 ────────────┘
```

本机与客户环境共用同步、校验、事实表、权限、证据和回答代码。区别只在来源类型与部署配置；模拟数据在界面和回答中明确标为“演示模拟数据”。

## 服务职责

| 服务 | 职责 | 关键限制 |
|---|---|---|
| `scheduler` | 每日 02:00 创建同步 Job | advisory lock；窗口幂等；不直接读源库 |
| `ingestion-worker` | 飞书只读拉取、Source 校验、分域暂存和激活 | 客户源库只读；单域失败保留上一版本 |
| `file-worker` | PDF/DOCX/XLSX/PPTX 解析、分块、512 维向量 | 不 OCR；不跨会话；删除文件级联删除块 |
| `mcp-hub` | 11 个参数化经营查询 | 仅接受短时 HMAC 范围令牌；拒绝跨事业部 |
| `hermes-runtime` | `hermes-agent 0.19.0` 路由与表述 | 仅接 Anspire 固定网关；仅 `context_engine` 空工具集；禁用 Shell/浏览器/联网/规则修改 |
| `worker` | 路由、澄清、调用受控工具、保存回答和证据 | 不把数据库连接交给模型 |

## 11 个经营工具

1. `list_query_scopes`
2. `get_overall_business`
3. `get_target_completion`
4. `get_opportunity_funnel`
5. `get_sales_forecast`
6. `get_customer_status`
7. `get_delivery_status`
8. `get_finance_margin`
9. `get_collection_aging`
10. `get_organization_performance`
11. `get_daily_changes`

每次工具调用同时返回结构化数据、事业部范围、分域新鲜度和行/聚合证据。Hermes 只负责路由和表述，不生成数据库查询，也不拥有用户权限。

## 文件闭环

- 支持 PDF、DOCX、XLSX、PPTX；旧版 Office、加密文件、图片和 OCR 明确不支持。
- 上传后创建 `file.extract` Job，前端展示真实 queued/running/completed/failed 状态。
- PDF 引用页码，XLSX 引用工作表和单元格区域，PPTX 引用幻灯片，DOCX 引用段落。
- 检索必须同时满足企业、上传用户、当前会话关联和未删除条件。
- 原文件删除后，加密对象删除，提取记录和向量通过数据库级联删除。

## Anspire 单一模型通道

产品只接入 Anspire，不提供其他模型供应商、自定义 OpenAI 兼容地址或运行时降级通道。正式网关固定为 `https://open-gateway.anspire.ai/v6`。后端以版本内受控目录纳入 Anspire 全量 53 个模型；其中 39 个聊天与推理模型可供经营回答选择，图像、视频、Embedding 与 Rerank 模型保留能力标识但不能误入聊天接口。管理员无法把企业凭证转发到任意地址。

企业管理员或 FDE 登录管理端后，在“模型服务”中完成：选择模型、录入 API Key、保存、连接测试、启用。API Key 使用企业 ID 绑定的 AES-256-GCM 加密后保存于产品数据库，页面只返回尾号掩码；明文只在单次测试或回答请求期间解密并通过内部签名链传递，不进入浏览器存储、环境文件、容器常驻配置、日志或审计元数据。模型或密钥发生变化时自动停用，必须重新测试成功后才能启用。

## 基础设施凭证

| 文件/变量 | 用途 |
|---|---|
| `secrets/integration_encryption_key` | 加密企业 Anspire API Key 的 32 字节主密钥 |
| `secrets/integration_encryption_key_ring` | 只读历史集成密钥环，用于轮换期间解密旧版企业凭证 |
| `secrets/hermes_runtime_hmac_key` | API/Worker 与 Hermes Runtime 的内部请求签名 |
| `secrets/source_database_url` | 客户外部只读 Source URL |
| `secrets/capability_hmac_key` | MCP 短时能力令牌签名 |
| `secrets/feishu_runtime_secret` | 本机演示运行期只读飞书凭证 |
| `secrets/feishu_provisioning_secret` | 本机一次性发布凭证，不挂载常驻服务 |

所有基础设施密钥文件为 `0600`，不进入 Git、环境模板、镜像或日志。企业尚未配置并启用 Anspire 时，管理端明确显示“未配置”，回答任务明确失败，不回退到其他模型或伪造内容。

## FDE 操作

```bash
# 本机确定性重建与正式入库链
./scripts/rebuild-demo-source.sh local-demo demo-enterprise "REBUILD local-demo/demo-enterprise"
./scripts/configure-source.sh local-demo demo-enterprise "演示模拟数据"
./scripts/sync-now.sh local-demo demo-enterprise

# 可选：发布模拟商机到飞书
./scripts/publish-feishu-demo.sh local-demo demo-enterprise

# 客户首次接入
./scripts/configure-source.sh customer-template customer "客户认可的数据源名称"
./scripts/sync-now.sh customer-template customer
```

数据源注册会验证 PostgreSQL 版本、Schema 版本、缺失表列、只读事务、高权限角色与 TLS。人工同步只创建可审计 Job；真实执行仍由租约 Worker 完成。

## 新鲜度与失败语义

`opportunity`、`delivery`、`collection`、`target` 四个域分别记录来源、数据集版本、记录数、最近成功同步、数据截止时间、当前状态和最近错误。同步先写新 `sync_run_id`，完整校验后才把该域 `is_current` 原子切换；失败域不清空旧事实。

工作台通过 `/api/v1/data-capabilities` 显示数据域状态，通过会话 SSE 恢复消息进度。回答保存路由记录、结构化结果、来源时间和 `MessageEvidence`；用户可下钻查看证据，而不是只相信自然语言。

## 明确不在本阶段

任意数据库/任意表映射、原始 CRM 直连、产品侧脱敏、互联网研究、自动简报、飞书消息推送、除 Anspire 模型配置外的完整管理端、OCR、离线安装包仍未加入。不得在现场把这些能力描述为已交付。
