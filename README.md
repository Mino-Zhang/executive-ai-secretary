# 董事长 AI 秘书

面向企业高层的经营问数与决策工作台。仓库同时保留已冻结的交互 Demo，以及生产化第一阶段的真实身份、权限、数据与运维底座；两者通过明确运行模式隔离，生产模式绝不回退到固定演示答案。

## 两种运行形态

| 形态 | 用途 | 数据与账号 |
| --- | --- | --- |
| `demo-v1.0.0` | 产品体验评审和 UI 演示 | 确定性脱敏样本；已冻结为 Git Tag 与 GitHub Release |
| Production Foundation | 本机真实演示与客户部署模板 | PostgreSQL、真实 Session/RBAC/数据范围、加密文件、审计、备份 |

线上 Demo 保持原样。生产化工作位于 `codex/production-foundation` 分支，不会修改或重新部署线上 Sites 版本。

## 冻结 Demo

要求 Node.js `>=22.13.0`：

```bash
npm ci
npm run dev
```

默认 `NEXT_PUBLIC_APP_MODE=demo`，仅供演示。其固定账号、数据和模拟任务不能用于真实客户环境。

## 本机生产底座

要求 Docker Desktop 29+、Docker Compose、OpenSSL 与 curl。最短启动路径：

```bash
./scripts/prepare-env.sh local-demo
./scripts/start.sh local-demo
./scripts/bootstrap-admin.sh local-demo admin@example.com "企业管理员" "演示企业" demo-enterprise
./scripts/create-executive.sh local-demo demo-enterprise chairman@example.com "董事长" enterprise
./scripts/seed-demo.sh local-demo demo-enterprise "SEED local-demo/demo-enterprise"
./scripts/smoke-test.sh local-demo
```

入口为 <http://127.0.0.1:8080>，只监听本机回环地址。初始化脚本交互读取一次性强密码，不提供默认生产账号或默认口令。

客户空模板使用完全独立的 `customer-template` 环境，入口为 <http://127.0.0.1:8180>；它与本机 Demo 不共享数据库、卷、密钥、备份或账号，并在多层校验中禁止写入 Demo 数据。

完整说明见 [生产化第一阶段文档](./docs/production/README.md) 和 [本机安装手册](./docs/production/local-install.md)。

## 质量门

```bash
npm run lint
npm run typecheck
npm test
npm run security:audit

cd services/api
uv sync --frozen --extra dev
uv run ruff check .
uv run pytest
```

基础设施静态门禁：

```bash
./scripts/test-infra.sh
```

CI 在发布镜像前执行前后端测试、迁移检查、依赖审计、密钥扫描和容器构建。运行期密钥、数据库、文件与备份均位于 Git 忽略目录。

## 第一阶段边界

第一阶段完成身份、权限、Session、事业部数据范围、核心领域数据库、正式 API、加密文件、任务队列、日志、限流、备份与 CI/CD。真实经营数据源、Hermes/MCP、文件正文解析、模型回答、主动简报和飞书推送仍属于后续阶段；未配置能力会明确失败，不会用固定样本伪装成功。离线安装包按约定在第五阶段结束后制作。
