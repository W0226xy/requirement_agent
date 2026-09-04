import {
  CheckOutlined,
  FileOutlined,
  LoadingOutlined,
  PaperClipOutlined,
  RobotOutlined,
  SendOutlined,
  UserOutlined,
} from "@ant-design/icons";
import {
  Alert,
  AutoComplete,
  Button,
  Card,
  Divider,
  Input,
  List,
  message,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, queryString } from "../api";
import { EmptyBlock, ErrorBlock, StatusTag, formatDate } from "../components";
import { useSession } from "../session";
import type {
  PageResponse,
  ProposedOperation,
  Requirement,
  ReviewTask,
  SourceRecord,
} from "../types";

type IngestionResponse = {
  source: SourceRecord;
  replayed: boolean;
};

type Draft = {
  task: ReviewTask;
  source: SourceRecord;
  title: string;
  module: string;
  description: string;
  criteria: string;
};

const activeStatuses = new Set([
  "received",
  "parsing",
  "extracted",
  "retrieving",
  "analyzing",
]);

const progressText: Record<string, string> = {
  received: "原始需求已保存，等待后台处理",
  parsing: "正在读取正文和附件",
  extracted: "已提取结构化需求",
  retrieving: "正在检索相似历史需求",
  analyzing: "正在分析重复、冲突、关联和风险",
  pending_review: "分析完成，请确认是否保留",
  versioned: "已通过审核并生成正式版本",
  rejected: "你已选择不保留该需求",
  returned: "需求已退回补充",
};

export function ConversationPage() {
  const session = useSession();
  const navigate = useNavigate();
  const [sources, setSources] = useState<SourceRecord[]>([]);
  const [reviews, setReviews] = useState<Map<number, ReviewTask>>(new Map());
  const [existingModules, setExistingModules] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const loadConversation = useCallback(async () => {
    try {
      const [sourcePage, reviewPage, requirementPage] = await Promise.all([
        api<PageResponse<SourceRecord>>(
          `/api/v1/source-records${queryString({
            page: 1,
            page_size: 100,
            submitter_id: session.actorId,
          })}`,
        ),
        api<PageResponse<ReviewTask>>("/api/v1/review-tasks?page=1&page_size=100"),
        api<PageResponse<Requirement>>("/api/v1/requirements?page=1&page_size=100"),
      ]);
      setSources([...sourcePage.items].reverse());
      setReviews(
        new Map(reviewPage.items.map((review) => [review.source_record_id, review])),
      );
      setExistingModules(
        Array.from(
          new Set(requirementPage.items.flatMap((requirement) => requirement.functional_modules)),
        ).sort(),
      );
      setError(null);
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, [session.actorId]);

  useEffect(() => {
    void loadConversation();
    const timer = window.setInterval(() => void loadConversation(), 3_000);
    return () => window.clearInterval(timer);
  }, [loadConversation]);

  const hasActiveTurn = useMemo(
    () => sources.some((source) => activeStatuses.has(source.processing_status)),
    [sources],
  );

  async function sendRequirement() {
    if (!text.trim() && !file) {
      void message.error("请输入需求描述或选择附件");
      return;
    }
    setSending(true);
    setError(null);
    try {
      const idempotencyKey = `web-chat-${crypto.randomUUID()}`;
      if (file) {
        const form = new FormData();
        form.set("submitter_id", session.actorId);
        form.set("submitter_name", session.actorName);
        form.set("raw_text", text.trim());
        form.set("file", file);
        await api<IngestionResponse>("/api/v1/files", {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: form,
        });
      } else {
        await api<IngestionResponse>("/api/v1/ingestions", {
          method: "POST",
          headers: { "Idempotency-Key": idempotencyKey },
          body: JSON.stringify({
            submitter_id: session.actorId,
            submitter_name: session.actorName,
            raw_text: text.trim(),
            raw_metadata: { input_surface: "conversation" },
          }),
        });
      }
      setText("");
      setFile(null);
      void message.success("需求已保存，AI 正在分析");
      await loadConversation();
    } catch (caught) {
      setError(caught);
    } finally {
      setSending(false);
    }
  }

  function openDraft(source: SourceRecord, task: ReviewTask) {
    const extraction = task.extraction_snapshot;
    const modules = stringArray(extraction.functional_modules);
    setDraft({
      source,
      task,
      title: String(extraction.requirement_summary ?? source.raw_text.slice(0, 80)),
      module: modules[0] ?? "未分类",
      description: String(extraction.requirement_description ?? source.raw_text),
      criteria: stringArray(extraction.acceptance_criteria).join("\n"),
    });
  }

  async function approveDraft() {
    if (!draft || !draft.title.trim() || !draft.description.trim()) {
      void message.error("需求标题和描述不能为空");
      return;
    }
    setSubmitting(true);
    try {
      const operation: ProposedOperation = {
        operation: "add",
        feature_key: null,
        content: {
          module: draft.module.trim() || "未分类",
          feature_title: draft.title.trim(),
          feature_description: draft.description.trim(),
          acceptance_criteria: draft.criteria
            .split("\n")
            .map((item) => item.trim())
            .filter(Boolean),
        },
        source_record_id: draft.source.id,
        reason: "用户在需求对话中审核并确认保留",
      };
      await api(
        `/api/v1/review-tasks/${draft.task.id}/approve`,
        {
          method: "POST",
          body: JSON.stringify({
            decision: "create",
            title: draft.title.trim(),
            target_requirement_id: null,
            operations: [operation],
            comment: "用户确认保留该需求",
          }),
        },
        session,
      );
      setDraft(null);
      void message.success("需求已保留并生成正式版本");
      await loadConversation();
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  }

  async function reject(task: ReviewTask) {
    try {
      await api(
        `/api/v1/review-tasks/${task.id}/reject`,
        {
          method: "POST",
          body: JSON.stringify({ comment: "用户在需求对话中选择不保留" }),
        },
        session,
      );
      void message.success("该需求已标记为不保留");
      await loadConversation();
    } catch (caught) {
      setError(caught);
    }
  }

  async function retry(source: SourceRecord) {
    try {
      await api(`/api/v1/source-records/${source.id}/reanalyze`, { method: "POST" });
      void message.success("已重新提交分析");
      await loadConversation();
    } catch (caught) {
      setError(caught);
    }
  }

  return (
    <div className="conversation-page">
      <div className="conversation-heading">
        <div>
          <Typography.Title level={2}>提出需求</Typography.Title>
          <Typography.Text type="secondary">
            描述你的想法，AI 会检索历史需求并分析冲突和风险，最终由你决定是否保留。
          </Typography.Text>
        </div>
        {hasActiveTurn && <Tag icon={<LoadingOutlined />} color="processing">AI 分析中</Tag>}
      </div>

      {error !== null && <ErrorBlock error={error} />}
      <div className="conversation-stream">
        {loading ? (
          <div className="state-block"><Spin size="large" /></div>
        ) : sources.length === 0 ? (
          <EmptyBlock description="还没有需求，先在下方描述一个想法吧" />
        ) : (
          sources.map((source) => (
            <ConversationTurn
              key={source.id}
              source={source}
              review={reviews.get(source.id)}
              onRetain={openDraft}
              onReject={(task) => void reject(task)}
              onRetry={() => void retry(source)}
              onAdvanced={(task) => navigate(`/reviews/${task.id}`)}
              onOpenRequirement={(task) => {
                if (task.target_requirement_id) {
                  navigate(`/requirements/${task.target_requirement_id}`);
                }
              }}
            />
          ))
        )}
      </div>

      <Card className="conversation-composer">
        <Input.TextArea
          value={text}
          autoSize={{ minRows: 3, maxRows: 8 }}
          placeholder="例如：报表页需要支持导出 PDF，文件中要包含当前筛选条件……"
          onChange={(event) => setText(event.target.value)}
          onPressEnter={(event) => {
            if (!event.shiftKey) {
              event.preventDefault();
              void sendRequirement();
            }
          }}
        />
        <div className="composer-actions">
          <label className="file-picker">
            <PaperClipOutlined />
            <span>{file ? file.name : "添加 PDF、Word 或截图"}</span>
            <input
              type="file"
              accept=".pdf,.docx,.png,.jpg,.jpeg"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <Space>
            {file && <Button type="text" onClick={() => setFile(null)}>移除附件</Button>}
            <Button
              type="primary"
              icon={<SendOutlined />}
              loading={sending}
              onClick={() => void sendRequirement()}
            >
              提交需求
            </Button>
          </Space>
        </div>
      </Card>

      <Modal
        open={draft !== null}
        title="修改并确认保留"
        okText="保留并生成正式需求"
        cancelText="取消"
        confirmLoading={submitting}
        onOk={() => void approveDraft()}
        onCancel={() => setDraft(null)}
        width={720}
      >
        {draft && (
          <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <Alert
              type="info"
              showIcon
              message="此处内容将作为确定性版本操作提交，AI 不会直接修改正式需求。"
            />
            <label>
              <Typography.Text strong>需求标题</Typography.Text>
              <Input
                value={draft.title}
                onChange={(event) => setDraft({ ...draft, title: event.target.value })}
              />
            </label>
            <label>
              <Typography.Text strong>功能模块</Typography.Text>
              <AutoComplete
                value={draft.module}
                options={existingModules.map((module) => ({
                  value: module,
                  label: module,
                }))}
                placeholder="优先选择已有模块，也可输入新模块"
                filterOption={(input, option) =>
                  String(option?.value ?? "").toLowerCase().includes(input.toLowerCase())
                }
                onChange={(value) => setDraft({ ...draft, module: value })}
              />
            </label>
            <label>
              <Typography.Text strong>需求描述</Typography.Text>
              <Input.TextArea
                value={draft.description}
                autoSize={{ minRows: 4, maxRows: 10 }}
                onChange={(event) =>
                  setDraft({ ...draft, description: event.target.value })
                }
              />
            </label>
            <label>
              <Typography.Text strong>验收标准（每行一条）</Typography.Text>
              <Input.TextArea
                value={draft.criteria}
                autoSize={{ minRows: 3, maxRows: 8 }}
                onChange={(event) => setDraft({ ...draft, criteria: event.target.value })}
              />
            </label>
          </Space>
        )}
      </Modal>
    </div>
  );
}

function ConversationTurn({
  source,
  review,
  onRetain,
  onReject,
  onRetry,
  onAdvanced,
  onOpenRequirement,
}: {
  source: SourceRecord;
  review?: ReviewTask;
  onRetain: (source: SourceRecord, review: ReviewTask) => void;
  onReject: (review: ReviewTask) => void;
  onRetry: () => void;
  onAdvanced: (review: ReviewTask) => void;
  onOpenRequirement: (review: ReviewTask) => void;
}) {
  const failed = source.processing_status.endsWith("_failed");
  return (
    <div className="conversation-turn">
      <div className="chat-row chat-row-user">
        <div className="chat-avatar"><UserOutlined /></div>
        <div className="chat-bubble user-bubble">
          <Typography.Paragraph>{source.raw_text || "请分析附件中的需求"}</Typography.Paragraph>
          {source.attachments.map((attachment) => (
            <Tag icon={<FileOutlined />} key={attachment.id}>{attachment.file_name}</Tag>
          ))}
          <div className="chat-meta">{source.source_key} · {formatDate(source.received_at)}</div>
        </div>
      </div>
      <div className="chat-row chat-row-assistant">
        <div className="chat-avatar assistant-avatar"><RobotOutlined /></div>
        <div className="chat-bubble assistant-bubble">
          {failed ? (
            <>
              <Alert
                type="error"
                showIcon
                message="分析未完成"
                description={`当前状态：${source.processing_status}。原始输入已经安全保存。`}
              />
              <Button className="chat-action" onClick={onRetry}>重新分析</Button>
            </>
          ) : !review ? (
            <Space>
              <Spin size="small" />
              <span>{progressText[source.processing_status] ?? source.processing_status}</span>
            </Space>
          ) : (
            <AnalysisReply
              source={source}
              review={review}
              onRetain={onRetain}
              onReject={onReject}
              onAdvanced={onAdvanced}
              onOpenRequirement={onOpenRequirement}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function AnalysisReply({
  source,
  review,
  onRetain,
  onReject,
  onAdvanced,
  onOpenRequirement,
}: {
  source: SourceRecord;
  review: ReviewTask;
  onRetain: (source: SourceRecord, review: ReviewTask) => void;
  onReject: (review: ReviewTask) => void;
  onAdvanced: (review: ReviewTask) => void;
  onOpenRequirement: (review: ReviewTask) => void;
}) {
  const extraction = review.extraction_snapshot;
  const analysis = review.analysis_snapshot;
  const pending = review.review_status === "pending";
  return (
    <>
      <div className="analysis-title">
        <div>
          <Typography.Title level={4}>
            {String(extraction.requirement_summary ?? "需求分析")}
          </Typography.Title>
          <StatusTag value={analysis.conflict_status ?? "none"} />
        </div>
        <StatusTag value={review.review_status} />
      </div>
      <Typography.Paragraph>
        {String(extraction.requirement_description ?? "未生成需求描述")}
      </Typography.Paragraph>
      <Space wrap>
        {stringArray(extraction.functional_modules).map((module) => (
          <Tag key={module}>{module}</Tag>
        ))}
      </Space>

      <Divider>分析结论</Divider>
      <List
        size="small"
        dataSource={analysis.conflicts ?? []}
        locale={{ emptyText: "未发现明确重复或冲突" }}
        renderItem={(conflict) => (
          <List.Item>
            <List.Item.Meta
              title={
                <Space>
                  <StatusTag value={String(conflict.type ?? "related")} />
                  {String(conflict.requirement_id ?? "")}
                </Space>
              }
              description={`${String(conflict.description ?? "")}；依据：${String(conflict.evidence ?? "")}`}
            />
          </List.Item>
        )}
      />
      {!!analysis.risks?.length && (
        <>
          <Divider>风险</Divider>
          <List
            size="small"
            dataSource={analysis.risks}
            renderItem={(risk) => (
              <List.Item>
                <Space align="start">
                  <StatusTag value={String(risk.level ?? "medium")} />
                  <span>{String(risk.description ?? "")}</span>
                </Space>
              </List.Item>
            )}
          />
        </>
      )}
      {!!analysis.clarification_questions?.length && (
        <Alert
          type="warning"
          showIcon
          message="需要进一步确认"
          description={analysis.clarification_questions.join("；")}
        />
      )}
      {pending ? (
        <Space wrap className="chat-actions">
          <Button
            type="primary"
            icon={<CheckOutlined />}
            onClick={() => onRetain(source, review)}
          >
            修改后保留
          </Button>
          <Popconfirm
            title="确定不保留这个需求吗？"
            description="原始输入和审核记录仍会保留，但不会生成正式版本。"
            okText="不保留"
            cancelText="取消"
            onConfirm={() => onReject(review)}
          >
            <Button danger>不保留</Button>
          </Popconfirm>
          <Button onClick={() => onAdvanced(review)}>合并已有需求 / 高级审核</Button>
        </Space>
      ) : review.review_status === "approved" ? (
        <Button type="link" onClick={() => onOpenRequirement(review)}>
          已保留，查看正式需求
        </Button>
      ) : (
        <Typography.Text type="secondary">
          {review.review_status === "rejected" ? "你已选择不保留该需求。" : "该需求已退回处理。"}
        </Typography.Text>
      )}
    </>
  );
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}
