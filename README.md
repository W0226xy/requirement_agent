# 多渠道输入需求管理 Agent

这是一个采用 FastAPI、Celery、PostgreSQL/pgvector、Redis、MinIO 和 React
构建的模块化单体应用。当前完成阶段 6 飞书集成。

## 环境要求

- Docker 24+ 与 Docker Compose v2
- 可选：Python 3.12、Node.js 22

项目现有 `.venv` 是 Python 3.10，不能用于本项目。推荐直接使用 Docker，
或重新创建 Python 3.12 虚拟环境。

## 首次启动

```bash
cp .env.example .env
# 修改 .env 中的密码和模型配置，不要提交 .env
docker compose build
docker compose up -d
docker compose exec api alembic upgrade head
```

访问地址：

- 管理后台：<http://localhost:5173>
- OpenAPI：<http://localhost:8000/docs>
- 存活检查：<http://localhost:8000/health/live>
- 就绪检查：<http://localhost:8000/health/ready>
- MinIO 控制台：<http://localhost:9001>

查看服务状态和日志：

```bash
docker compose ps
docker compose logs --tail=100 api worker
```

停止服务：

```bash
docker compose down
```

`docker compose down` 不会删除数据卷。除非明确需要清空本地开发数据，否则不要添加
`--volumes`。

## 数据库迁移

```bash
docker compose exec api alembic upgrade head
docker compose exec api alembic current
```

PostgreSQL 初始化脚本和首个 Alembic 迁移都会幂等地启用 `vector` 扩展。

迁移创建：

- `source_record`：不可覆盖的原始输入。
- `source_attachment`：附件身份、MinIO 路径和解析结果。
- `audit_log`：不可修改的关键操作记录。
- `analysis_result`、`requirement_embedding`：AI 调用记录和检索投影。
- `review_task`：持久化审核材料、决策及提交结果。
- `requirement`、`requirement_version`、`requirement_feature`：需求主表和完整版本快照。
- `feature_lineage`：功能新增、修改、删除和恢复的原始输入来源。

PostgreSQL 触发器禁止修改原始内容、附件身份和审计日志，也禁止删除这些记录。

## 提交原始需求

### Web 表单输入

`Idempotency-Key` 表示渠道事件 ID。同一个 Key 和相同内容重复提交时返回原记录；
同一个 Key 携带不同内容时返回 `409 IDEMPOTENCY_CONFLICT`。

```bash
curl -X POST http://localhost:8000/api/v1/ingestions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: web-form-example-0001' \
  -d '{
    "submitter_id": "user-001",
    "submitter_name": "测试用户",
    "raw_text": "报表页面需要支持导出 PDF",
    "raw_metadata": {"module": "reporting"}
  }'
```

首次接收返回 HTTP `202`，重复提交返回 HTTP `200`，并将 `replayed` 设为 `true`。

### 上传 PDF、DOCX 或截图

```bash
curl -X POST http://localhost:8000/api/v1/files \
  -H 'Idempotency-Key: file-example-0001' \
  -F 'submitter_id=user-001' \
  -F 'submitter_name=测试用户' \
  -F 'raw_text=附件中的产品需求' \
  -F 'file=@./requirement.pdf;type=application/pdf'
```

允许的格式：

- PDF：`application/pdf`
- Word：`application/vnd.openxmlformats-officedocument.wordprocessingml.document`
- 截图：`image/png`、`image/jpeg`

默认单文件上限为 50 MiB，由 `MAX_UPLOAD_SIZE_BYTES` 配置。后端同时检查扩展名、
MIME 和文件签名，客户端声明的类型不能绕过校验。

### 查询原始记录

```bash
curl 'http://localhost:8000/api/v1/source-records?page=1&page_size=20'
curl 'http://localhost:8000/api/v1/source-records/1'
curl 'http://localhost:8000/api/v1/source-records?channel_type=document'
```

### 飞书事件订阅

在 `.env` 配置 `FEISHU_APP_ID`、`FEISHU_APP_SECRET`、
`FEISHU_VERIFICATION_TOKEN` 和 `FEISHU_ENCRYPT_KEY`，并将飞书事件订阅地址设置为：

```text
POST https://<公开域名>/api/v1/connectors/feishu/events
```

接口同步完成 URL verification、verification token 和 v2 callback signature 校验，然后立即
返回并把 `im.message.receive_v1` 事件交给 Celery。文本消息的 JSON `content` 会转换为原始
需求；图片和文件使用租户访问令牌从飞书开放平台下载，经统一附件大小、扩展名、MIME 和
文件签名校验后写入 MinIO。`event_id` 是渠道幂等键。

租户令牌会在内存中缓存并在过期前刷新。开放平台请求默认超时 10 秒，可通过
`FEISHU_TIMEOUT_SECONDS` 调整；私有化部署可通过 `FEISHU_BASE_URL` 更改开放平台地址。
凭据、verification token、encrypt key 和租户访问令牌不会写入来源元数据或错误消息。
当前版本会安全拒绝 `encrypt` 加密回调；请在飞书后台启用 v2 签名，但不要启用消息体加密。

### 异步解析状态

输入持久化后才会投递 Celery 任务：

```text
received → parsing
                 ├─ 附件成功：parsed
                 └─ 附件失败：failed，原始记录进入 parse_failed
```

解析完成后会继续执行阶段 3 分析，并创建待处理审核任务。

查看 Worker：

```bash
docker compose logs -f worker
```

Worker 使用 `source_processing` 队列；解析失败最多自动重试三次。任务和解析操作均为
幂等设计，已解析附件不会再次写入。

## AI 分析工作流

阶段 3 使用 LangGraph 编排：

```text
结构化提取
→ 字段/全文/向量混合检索
→ 重复、关联、冲突、替代和依赖分析
→ 风险分析与标准变更建议
→ pending_review
```

LangGraph 只编排分析步骤。业务状态、输入、输出、错误和耗时都保存在 PostgreSQL。
AI 不会创建或修改正式需求版本。

### 模型配置

聊天和 Embedding 可以来自两个不同的 OpenAI 兼容服务，在 `.env` 中分别配置：

```text
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=replace-with-your-chat-api-key
LLM_MODEL=replace-with-your-chat-model
EMBEDDING_BASE_URL=https://embedding-provider.example/v1
EMBEDDING_API_KEY=replace-with-your-embedding-api-key
EMBEDDING_MODEL=replace-with-your-embedding-model
EMBEDDING_DIMENSION=1024
LLM_TIMEOUT_SECONDS=60
LLM_MAX_RETRIES=2
```

`EMBEDDING_DIMENSION` 在首次执行阶段 3 迁移时决定 PostgreSQL `vector(n)` 的维度，
之后不能只修改环境变量；更换维度需要新增迁移并重建向量数据和 HNSW 索引。
当前项目的实际 Embedding 服务返回 1024 维，迁移 `20260904_0005` 将已有开发数据库从
1536 维调整为 1024 维。部署时该值必须与模型服务的真实输出维度严格一致。

聊天客户端只访问 `{LLM_BASE_URL}/chat/completions`，使用 `LLM_API_KEY` 和
`LLM_MODEL`。Embedding 客户端只访问 `{EMBEDDING_BASE_URL}/embeddings`，使用
`EMBEDDING_API_KEY` 和 `EMBEDDING_MODEL`。两个客户端、协议和缓存工厂彼此独立。

API 和 Worker 通过 Docker Compose 的同一个 `.env` 配置运行。修改任一模型配置后需
重建或重启两个服务：

```bash
docker compose up -d --force-recreate api worker
```

API Key 不得写入源码或提交到 Git，也不会写入异常信息、运行日志或
`analysis_result` 审计记录。

### 结构化输出保障

- 模型仅返回 JSON。
- Pydantic 拒绝未知字段、错误枚举和 `null` 数组。
- 第一次失败后将具体校验错误反馈给模型。
- 最多自动纠错两次，即总计最多三次调用。
- 每次尝试都写入 `analysis_result`，包括输入、原始输出、结果、模型、提示词版本、
  耗时和错误。
- 连续失败后来源状态进入 `extraction_failed` 或 `analysis_failed`。

测试使用 `FakeLLM`，不会访问真实模型服务。

固定评测集位于 `tests/ai_evaluation/cases.json`，覆盖完全重复、语义重复、可共存关联、
明确矛盾、替代、依赖、信息不足和无关需求。

### 查询 AI 调用

```bash
curl 'http://localhost:8000/api/v1/analysis-results?page=1&page_size=20'
curl 'http://localhost:8000/api/v1/analysis-results?source_record_id=1'
curl 'http://localhost:8000/api/v1/analysis-results?failed_only=true'
```

重新分析：

```bash
curl -X POST http://localhost:8000/api/v1/source-records/1/reanalyze
```

### 混合检索

默认排序权重来自环境变量：

```text
0.4 × keyword_score + 0.4 × vector_score + 0.2 × business_field_score
```

三个权重必须合计为 `1.0`，候选数量限制为 10–20。全文检索使用 PostgreSQL GIN，
向量检索使用 pgvector HNSW。模型只能引用后端返回的候选 `requirement_key`。

```bash
curl -X POST http://localhost:8000/api/v1/search \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "报表导出 PDF",
    "query_modules": ["reporting"],
    "filter_modules": [],
    "statuses": ["active"]
  }'
```

`requirement_embedding` 是不可变检索投影，只索引正式版本；混合检索会过滤掉历史投影，
仅返回需求主表指向的当前版本。

## 人工审核和版本提交

AI 分析完成后会幂等创建一条 `review_task`。审核数据与正式版本严格分离，只有拥有
`reviewer` 或 `admin` 角色的请求才能批准、退回、驳回或要求重新分析：

```bash
curl 'http://localhost:8000/api/v1/review-tasks?review_status=pending'
curl 'http://localhost:8000/api/v1/review-tasks/1'
```

批准并创建新需求：

```bash
curl -X POST 'http://localhost:8000/api/v1/review-tasks/1/approve' \
  -H 'Content-Type: application/json' \
  -H 'X-Actor-ID: reviewer-001' \
  -H 'X-Actor-Role: reviewer' \
  -d '{
    "decision": "create",
    "title": "报表导出",
    "operations": [{
      "operation": "add",
      "feature_key": null,
      "content": {
        "module": "报表",
        "feature_title": "导出 PDF",
        "feature_description": "用户可以将报表导出为 PDF",
        "acceptance_criteria": ["点击导出后下载 PDF 文件"]
      },
      "source_record_id": 1,
      "reason": "新增导出能力"
    }],
    "comment": "审核通过"
  }'
```

合并到已有需求时使用 `"decision": "merge"` 并传入 `target_requirement_id`。审核人可在
提交前编辑 `operations`，但只能使用 `add`、`modify`、`delete`、`restore`。后端会
锁定审核任务和需求、验证功能状态、生成完整快照及差异、记录来源和审计，并在同一事务
中更新当前版本指针。重复提交同一审核任务会返回首次生成的版本，不会创建重复版本。

退回、驳回和重新分析：

```bash
curl -X POST 'http://localhost:8000/api/v1/review-tasks/1/return' \
  -H 'Content-Type: application/json' \
  -H 'X-Actor-ID: reviewer-001' \
  -H 'X-Actor-Role: reviewer' \
  -d '{"comment":"请补充验收条件"}'

curl -X POST 'http://localhost:8000/api/v1/review-tasks/1/reject' \
  -H 'Content-Type: application/json' \
  -H 'X-Actor-ID: reviewer-001' \
  -H 'X-Actor-Role: reviewer' \
  -d '{"comment":"不符合产品方向"}'
```

版本提交成功后，Worker 异步生成需求级和功能级 Embedding 投影。任务重复执行不会产生
重复投影。

### 查询正式需求与版本

```bash
curl 'http://localhost:8000/api/v1/requirements?page=1&page_size=20'
curl 'http://localhost:8000/api/v1/requirements/1'
curl 'http://localhost:8000/api/v1/requirements/1/versions'
curl 'http://localhost:8000/api/v1/requirements/1/versions/1'
curl 'http://localhost:8000/api/v1/requirements/1/diff'
curl 'http://localhost:8000/api/v1/requirements/1/diff?to_version=2'
```

需求详情按功能返回全部来源事件，包括来源记录、引入或变更版本、操作类型和证据。
`requirement_version`、`requirement_feature`、`feature_lineage` 由 PostgreSQL 触发器
禁止更新或删除；删除功能只会创建 `feature_status=deleted` 的新版本快照。

## 管理后台

浏览器打开 <http://localhost:5173>。当前使用开发身份登录：填写审核人 ID、显示名称并
选择 `reviewer` 或 `admin` 角色。浏览器只保存身份标识，后端仍会在所有审核操作上检查
角色请求头；生产环境需要在后续部署集成中替换为企业 SSO 或网关认证。

后台页面包括：

- `/chat`：默认需求对话页。支持输入文本或上传 PDF、Word、截图；实时显示处理进度，
  并在同一条对话中返回结构化需求、重复/冲突判断、风险和待确认问题。用户可修改标题、
  模块、描述及验收标准后保留需求，也可选择不保留，或进入高级审核合并已有需求。
- `/requirements`：正式需求列表、状态和模块筛选。
- `/requirements/{id}`：当前功能、完整来源链路、版本历史、差异和快照。
- `/reviews`：按状态查看人工审核任务。
- `/reviews/{id}`：并排查看原始资料、附件解析、AI 提取、候选需求、冲突、风险和问题；
  可编辑标准变更 JSON，然后创建需求、合并已有需求、批准、退回、驳回或重新分析。
- `/sources`、`/sources/{id}`：原始输入、附件解析结果和 AI 调用链。
- `/search`：普通字段、全文与向量混合检索。
- `/analysis`：AI 调用记录、失败任务和重试入口。
- `/connectors`：内置渠道状态和已启用的飞书接入说明。

Vite 将 `/api` 和 `/health` 代理到 `VITE_API_PROXY_TARGET`。Docker Compose 默认指向
`http://api:8000`，因此浏览器不需要单独配置 CORS。

### 对话式需求流程

1. 登录后默认进入“提出需求”。
2. 输入需求描述；也可以附加一个 PDF、DOCX、PNG 或 JPEG 文件。
3. 页面每三秒刷新一次处理状态，显示解析、提取、检索和分析进度。
4. AI 分析完成后，同一对话中展示需求摘要、历史候选、冲突证据、风险和待确认问题。
5. 选择“修改后保留”，确认正式标题、模块、描述及验收标准。后端使用标准 `add`
   操作创建版本，模型不能直接写正式需求。
6. 选择“不保留”时只更新审核状态，原始输入和分析记录仍会保存。
7. 需要合并已有需求或编辑复杂操作时，点击“合并已有需求 / 高级审核”。

页面刷新后会按当前登录用户的 `actorId` 重新加载历史对话，处理中的需求会继续自动刷新，
因此关闭浏览器不会中断后台 Celery 分析任务。

失败任务接口：

```bash
curl 'http://localhost:8000/api/v1/failed-jobs?page=1&page_size=20'
curl -X POST 'http://localhost:8000/api/v1/failed-jobs/12/retry' \
  -H 'X-Actor-ID: reviewer-001' \
  -H 'X-Actor-Role: reviewer'
```

前端质量检查：

```bash
npm --prefix apps/web install
npm --prefix apps/web run lint
npm --prefix apps/web run build
```

## 后端本地开发

```bash
python3.12 -m venv .venv312
source .venv312/bin/activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn apps.api.main:app --reload
```

Worker：

```bash
celery -A apps.worker.celery_app:celery_app worker --loglevel=INFO
```

## 前端本地开发

```bash
cd apps/web
npm install
npm run dev
```

## 质量检查和测试

使用 Python 3.12 容器：

```bash
docker compose run --rm api ruff check .
docker compose run --rm api mypy
docker compose run --rm api pytest -p no:cacheprovider
docker compose run --rm web npm run build
```

阶段 2 也可以在 Python 3.12 环境直接验证：

```bash
ruff check .
mypy
pytest
```

## 配置安全

- API Key、数据库密码和渠道密钥只通过环境变量或 Secret Manager 提供。
- `.env` 已加入 `.gitignore`。
- `.env.example` 中只有本地开发占位值，生产环境必须替换。
- `channel_connector.config_json` 后续只保存密钥引用，不保存明文 Token。

## 常见问题

### API 显示数据库未就绪

检查 PostgreSQL 状态并执行迁移：

```bash
docker compose ps postgres
docker compose exec api alembic upgrade head
```

### MinIO 未就绪

```bash
docker compose logs minio minio-init
```

确认 `.env` 中的 `MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY` 和 `MINIO_BUCKET`
没有在启动后被修改。修改已有实例凭据后可能需要重新创建本地开发容器。

### 端口冲突

检查本机的 `5173`、`5432`、`6379`、`8000`、`9000` 和 `9001` 端口，
或修改 `docker-compose.yml` 左侧的宿主机端口。

### Worker 无法连接 Redis

容器内必须使用主机名 `redis`，不能使用 `localhost`。运行：

```bash
docker compose logs --tail=100 worker redis
```
