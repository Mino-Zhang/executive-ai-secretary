# CI/CD 与发布

## Pull Request / 分支 CI

`.github/workflows/ci.yml` 执行：

1. 前端 lint、构建测试和生产依赖高危审计。
2. 后端 Ruff、Pytest 与真实 PostgreSQL Alembic 迁移。
3. 两套 Compose 配置、安全不变量、脚本语法和 Nginx 配置校验。
4. Gitleaks 密钥扫描和 Trivy 依赖/配置扫描。
5. 在全新 Compose project 中执行真实登录、首次改密、RBAC、加密文件上传下载、备份、数据变更与恢复演练。
6. Web、API、Worker 的 `linux/amd64` 与 `linux/arm64` 镜像构建。

CI 密钥均在 Job 内临时生成，不能使用本机或客户密钥。Checkout 禁止持久化 Git 凭据。

## 镜像发布

`.github/workflows/release-images.yml` 只接收 `production-v*` Tag 或从 `main` 人工明确触发。接收触发不等于获得发布能力：最前面的 `release_authorization` Job 没有 `packages: write` 或 `id-token: write`，并且必须同时满足两个条件：

1. Repository Variable `PRODUCTION_IMAGE_RELEASES_ENABLED` 由仓库管理员显式设为严格小写的 `true`。变量不存在、为空或其他值时直接失败。
2. 工作流用只读 `actions: read` 的 `GITHUB_TOKEN` 和固定的 GitHub REST API 版本 `2026-03-10` 查询 Environment API，确认 `production-images` 已经存在，有非空必需审批人、已启用 prevent self-review，并且定制部署来源有且仅有 branch 类型的 `main` 和 Tag 类型的 `production-v*`。同名但类型错误或额外的来源规则都不会通过。

Environment 查询在任何 Job 引用该 Environment 之前发生。因此 Environment 不存在时只会收到 404 并关闭发布，不会因 Workflow 引用而自动创建一个无保护的同名 Environment。只有当这个无发布权限的 Job 输出 `authorized=true` 时，带有 `packages: write` 和 `id-token: write` 的 `publish` Job 才会被创建并再次等待 Environment 审批。

通过发布授权后，工作流还会再次确认当前 commit 是 `origin/main` 的祖先；从其他分支人工触发或对不在主线的 commit 打 Tag 都会在发布前被拒绝。镜像：

- 使用不可变语义版本标签，不发布业务环境依赖的 `latest`
- 同时支持 `amd64` 与 `arm64`
- 生成 provenance 和 SBOM
- 使用 GitHub Actions OIDC 对发布后的镜像 digest 做 Cosign keyless 签名，签名注解同时固定组件、版本与 commit
- 不把 `.env`、runtime secrets、备份、Dify 工作目录或开发输出加入构建上下文

三个应用镜像不再被当成三个彼此独立的产物。同一受保护的 `publish` Job 完成全部构建后，生成 `release-bundle.json` 并用 Cosign keyless 签名为 `release-bundle.sigstore.json`。bundle 精确绑定：

- 语义版本、Git commit、源 ref、触发类型和 Workflow Run ID
- Web、API、Worker 三个 GHCR 镜像 digest
- PostgreSQL、Nginx、文件工具三个已审阅基础镜像 digest
- 唯一 Alembic head

这两个文件以 `executive-ai-release-<version>-<commit>-<run_id>` Workflow Artifact 发布。Artifact 名包含不可混淆的 commit 和 run ID；真正的信任根是 Sigstore bundle 内的签名、透明日志证据与 GitHub OIDC claims，部署端不依赖某个 Tag 当前指向的内容。

## GitHub 发布保护必须项

仓库管理员必须在 GitHub 中完成以下两组配置，否则不得执行首次生产发布：

1. 先建立名为 `production-images` 的 Environment，要求至少一名非发起人复核，开启 prevent self-review，选择 custom deployment branches and tags，并分别添加 branch 类型 `main` 与 Tag 类型 `production-v*`。GitHub 计划如果不支持私有仓库的必需审批人，则不得绕过门禁；应升级计划或转入支持该保护的组织仓库。
2. 完成并复核 Environment 保护后，最后由仓库管理员创建 Repository Variable `PRODUCTION_IMAGE_RELEASES_ENABLED=true`。不要提前创建该变量。只有该 Environment 后面的 `publish` Job 拥有 `packages: write` 和 `id-token: write`；授权检查 Job 只有 `actions: read` 和 `contents: read`，其他 Job 默认为只读。
3. 使用 Repository Ruleset 保护 `main` 和 `production-v*` Tag：`main` 必须通过 PR、必需 CI 与审批；只允许发布管理员创建 `production-v*`，禁止更新、强制修改或删除已有生产 Tag。

不应把 Environment 同名创建或一个普通审批步骤误认为启用。发布权限的完整开启顺序是：保护 Environment → 部署来源规则 → Repository Variable → 触发发布 → 非发起人审批。

## 应用构建基础镜像

三个应用 Dockerfile 的所有外部 `FROM` 和外部 `COPY --from` 都同时保留可读精确版本 Tag 并固定到已审阅 manifest-list digest：

| 用途 | 可读版本 | 已审阅 digest |
| --- | --- | --- |
| Web 构建与运行时 | Node.js `22.23.1-alpine3.24` | `sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2` |
| API / Worker 运行时 | Python `3.12.13-slim-trixie` | `sha256:57cd7c3a7a273101a6485ba99423ee568157882804b1124b4dd04266317710de` |
| API / Worker 依赖构建工具 | uv `0.10.5` | `sha256:476133fa2aaddb4cbee003e3dc79a88d327a5dc7cb3179b7f02cabd8fdfbcc6e` |

审阅时用 `docker buildx imagetools inspect` 在 Public ECR 官方镜像镜像源和 GHCR 上验证了三个 manifest-list digest，均同时包含 `linux/amd64` 与 `linux/arm64`。另外通过实际运行验证版本分别为 Node.js `v22.23.1`、Python `3.12.13` 和 uv `0.10.5`。`scripts/test-infra.sh` 会拒绝任何没有可读 Tag 或完整 SHA-256 digest 的外部构建源，并精确核对上述三个已审阅值。

所有第三方 GitHub Actions 均按完整 40 位 commit SHA 固定，行尾保留已审阅版本号。升级 Action 时必须从官方仓库核对 Tag 对应 SHA，以单独 PR 执行并重跑完整 CI，不得恢复使用 `@v4`、`@main` 等可变引用。

发布前必须满足：

- Demo Tag 与生产 Tag 语义分离
- CI 全绿，代码审阅完成
- 变更记录、迁移影响、回滚方法齐全
- 在空白 `customer-template` 做过一次安装验收
- 高危漏洞无未批准例外
- 获取 `production-images` Environment 复核人审批

## 本机交付

GitHub Actions 不直接连接演示者 Mac。现场 Demo 由演示者本机执行 `start.sh local-demo`；客户交付使用 `start-release.sh customer-template`，必须同时提供签名 release bundle 及 Sigstore 证据。启动脚本对六个 digest、版本、commit 和 Alembic head 做精确比对，再校验 OIDC 证书 claims 和三个应用镜像的发布注解，并强制 `--no-build`。这避免把本机暴露成远程部署目标，也避免交叉版本镜像或未审阅基础镜像混入客户环境。
