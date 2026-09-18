import {
  CheckOutlined,
  DeleteOutlined,
  EditOutlined,
  FileOutlined,
  LoadingOutlined,
  MenuOutlined,
  MessageOutlined,
  PaperClipOutlined,
  PlusOutlined,
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
  Drawer,
  Grid,
  Input,
  List,
  message,
  Modal,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Tooltip,
  Typography,
} from "antd";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { EmptyBlock, ErrorBlock, StatusTag, formatDate } from "../components";
import { useSession } from "../session";
import type {
  Conversation,
  ConversationMessage,
  CreateConversationMessageResponse,
  PageResponse,
  ProposedOperation,
  Requirement,
  ReviewTask,
  SourceRecord,
} from "../types";
import { applyReviewTaskUpdate } from "./conversationReviewUpdate";

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

function sortConversations(items: Conversation[]) {
  return [...items].sort(
    (left, right) =>
      new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
  );
}

export function ConversationPage() {
  const session = useSession();
  const navigate = useNavigate();
  const { conversationKey } = useParams<{ conversationKey: string }>();
  const screens = Grid.useBreakpoint();
  const isMobile = !screens.md;
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [existingModules, setExistingModules] = useState<string[]>([]);
  const [text, setText] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [sending, setSending] = useState(false);
  const [creating, setCreating] = useState(false);
  const [deletingKey, setDeletingKey] = useState<string | null>(null);
  const [loadingConversations, setLoadingConversations] = useState(true);
  const [loadingMessages, setLoadingMessages] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [renameTarget, setRenameTarget] = useState<Conversation | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [renaming, setRenaming] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const conversationStreamRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const listRequestGeneration = useRef(0);
  const messageRequestGeneration = useRef(0);
  const activeKeyRef = useRef(conversationKey);
  const bootstrapRunning = useRef(false);
  const creatingRef = useRef(false);
  const scrollMode = useRef<"initial" | "send" | null>(null);
  activeKeyRef.current = conversationKey;

  const activeConversation = useMemo(
    () =>
      conversations.find(
        (conversation) => conversation.conversation_key === conversationKey,
      ),
    [conversationKey, conversations],
  );
  const hasActiveTurn = useMemo(
    () =>
      messages.some((item) =>
        (item.source !== null && activeStatuses.has(item.source.processing_status)) ||
        item.chat_status === "pending" ||
        item.chat_status === "processing",
      ),
    [messages],
  );

  const loadConversationList = useCallback(
    async (showLoading = false, reportError = true): Promise<boolean> => {
      const generation = ++listRequestGeneration.current;
      if (showLoading) setLoadingConversations(true);
      try {
        const page = await api<PageResponse<Conversation>>(
          "/api/v1/conversations?page=1&page_size=100",
          {},
          session,
        );
        if (generation !== listRequestGeneration.current) return false;
        setConversations(sortConversations(page.items));
        setError(null);
        return true;
      } catch (caught) {
        if (reportError && generation === listRequestGeneration.current) setError(caught);
        return false;
      } finally {
        if (generation === listRequestGeneration.current) {
          setLoadingConversations(false);
        }
      }
    },
    [session],
  );

  const loadMessages = useCallback(
    async (key: string, showLoading = false, reportError = true): Promise<boolean> => {
      const generation = ++messageRequestGeneration.current;
      if (showLoading) setLoadingMessages(true);
      try {
        const page = await api<PageResponse<ConversationMessage>>(
          `/api/v1/conversations/${encodeURIComponent(key)}/messages?page=1&page_size=100`,
          {},
          session,
        );
        if (
          generation !== messageRequestGeneration.current ||
          activeKeyRef.current !== key
        ) {
          return false;
        }
        setMessages(page.items);
        setError(null);
        return true;
      } catch (caught) {
        if (
          reportError &&
          generation === messageRequestGeneration.current &&
          activeKeyRef.current === key
        ) {
          setError(caught);
        }
        return false;
      } finally {
        if (
          generation === messageRequestGeneration.current &&
          activeKeyRef.current === key
        ) {
          setLoadingMessages(false);
        }
      }
    },
    [session],
  );

  const createConversation = useCallback(
    async (replace = false) => {
      if (creatingRef.current) return null;
      creatingRef.current = true;
      setCreating(true);
      try {
        const created = await api<Conversation>(
          "/api/v1/conversations",
          { method: "POST", body: JSON.stringify({}) },
          session,
        );
        listRequestGeneration.current += 1;
        setConversations((current) =>
          sortConversations([
            created,
            ...current.filter(
              (item) => item.conversation_key !== created.conversation_key,
            ),
          ]),
        );
        setDrawerOpen(false);
        setError(null);
        navigate(`/chat/${created.conversation_key}`, { replace });
        return created;
      } catch (caught) {
        setError(caught);
        return null;
      } finally {
        creatingRef.current = false;
        setCreating(false);
        setLoadingConversations(false);
      }
    },
    [navigate, session],
  );

  useEffect(() => {
    void api<PageResponse<Requirement>>(
      "/api/v1/requirements?page=1&page_size=100",
      {},
      session,
    )
      .then((page) => {
        setExistingModules(
          Array.from(
            new Set(
              page.items.flatMap(
                (requirement) => requirement.functional_modules,
              ),
            ),
          ).sort(),
        );
      })
      .catch((caught: unknown) => setError(caught));
  }, [session]);

  useEffect(() => {
    if (conversationKey) {
      bootstrapRunning.current = false;
      return;
    }
    if (bootstrapRunning.current) return;
    bootstrapRunning.current = true;
    const bootstrap = async () => {
      setLoadingConversations(true);
      try {
        const page = await api<PageResponse<Conversation>>(
          "/api/v1/conversations?page=1&page_size=100",
          {},
          session,
        );
        const sorted = sortConversations(page.items);
        setConversations(sorted);
        if (sorted[0]) {
          navigate(`/chat/${sorted[0].conversation_key}`, { replace: true });
        } else {
          await createConversation(true);
        }
      } catch (caught) {
        setError(caught);
        setLoadingConversations(false);
        bootstrapRunning.current = false;
      }
    };
    void bootstrap();
  }, [conversationKey, createConversation, navigate, session]);

  useEffect(() => {
    if (!conversationKey) return;
    messageRequestGeneration.current += 1;
    setMessages([]);
    setLoadingMessages(true);
    scrollMode.current = "initial";
    void loadMessages(conversationKey, true);
    void loadConversationList(true);
  }, [conversationKey, loadConversationList, loadMessages]);

  useEffect(() => {
    if (!conversationKey || !hasActiveTurn) return;
    const timer = window.setInterval(() => {
      void loadMessages(conversationKey);
      void loadConversationList();
    }, 3_000);
    return () => window.clearInterval(timer);
  }, [
    conversationKey,
    hasActiveTurn,
    loadConversationList,
    loadMessages,
  ]);

  useEffect(() => {
    if (loadingMessages || scrollMode.current === null) return;
    const mode = scrollMode.current;
    scrollMode.current = null;
    const frame = window.requestAnimationFrame(() => {
      const stream = conversationStreamRef.current;
      if (stream) {
        stream.scrollTo({
          top: stream.scrollHeight,
          behavior: mode === "send" ? "smooth" : "auto",
        });
      }
    });
    return () => window.cancelAnimationFrame(frame);
  }, [loadingMessages, messages]);

  async function sendRequirement() {
    if (!conversationKey || (!text.trim() && !file)) {
      void message.error("请输入需求描述或选择附件");
      return;
    }
    const rawText = text.trim();
    const selectedFile = file;
    const temporaryUserKey = `local-user-${crypto.randomUUID()}`;
    const temporaryAssistantKey = `local-assistant-${crypto.randomUUID()}`;
    const now = new Date().toISOString();
    const temporaryUser: ConversationMessage = {
      message_key: temporaryUserKey,
      sequence_number: Number.MAX_SAFE_INTEGER - 1,
      role: "user",
      created_at: now,
      source: null,
      content: rawText || "请分析附件中的需求",
      tool_calls: [],
      references: [],
      latest_extraction: null,
      latest_conflict_analysis: null,
      review_task: null,
      chat_status: "submitted",
    };
    const temporaryAssistant: ConversationMessage = {
      ...temporaryUser,
      message_key: temporaryAssistantKey,
      sequence_number: Number.MAX_SAFE_INTEGER,
      role: "assistant",
      content: "正在检索历史需求…",
      chat_status: "pending",
    };
    // Render first. The server, not the browser, determines whether this is a query.
    scrollMode.current = "send";
    setMessages((current) => [...current, temporaryUser, temporaryAssistant]);
    setText("");
    setFile(null);
    if (fileInputRef.current) fileInputRef.current.value = "";
    setSending(true);
    setError(null);
    try {
      const form = new FormData();
      form.set("raw_text", rawText);
      form.set("actor_name", session.actorName);
      if (selectedFile) form.set("file", selectedFile);
      const result = await api<CreateConversationMessageResponse>(
        `/api/v1/conversations/${encodeURIComponent(conversationKey)}/messages`,
        {
          method: "POST",
          headers: {
            "Idempotency-Key": `web-chat-${crypto.randomUUID()}`,
          },
          body: form,
        },
        session,
      );
      if (activeKeyRef.current !== conversationKey) return;
      setMessages((current) =>
        [
          ...current.filter((item) =>
            item.message_key !== temporaryUserKey && item.message_key !== temporaryAssistantKey,
          ),
          result.message,
          ...(result.assistant_message ? [result.assistant_message] : []),
        ].sort(
          (left, right) => left.sequence_number - right.sequence_number,
        ),
      );
      void message.success(
        result.intent === "traceability_query"
          ? "查询回答已加入会话"
          : result.replayed ? "该消息已提交，正在同步分析状态" : "需求已保存，AI 正在分析",
      );
      await Promise.all([
        loadMessages(conversationKey),
        loadConversationList(),
      ]);
    } catch (caught) {
      setError(caught);
      setMessages((current) => current.map((item) =>
        item.message_key === temporaryAssistantKey
          ? { ...item, chat_status: "failed", content: "发送失败，请重试" }
          : item,
      ));
    } finally {
      setSending(false);
    }
  }

  async function clearModelContext() {
    if (!conversationKey) return;
    try {
      const updated = await api<Conversation>(
        `/api/v1/conversations/${encodeURIComponent(conversationKey)}/clear-context`,
        { method: "POST" },
        session,
      );
      setConversations((current) =>
        current.map((item) =>
          item.conversation_key === updated.conversation_key ? updated : item,
        ),
      );
      void message.success("已清空模型上下文，历史需求和审核记录仍会保留");
    } catch (caught) {
      setError(caught);
    }
  }

  function openDraft(source: SourceRecord, task: ReviewTask) {
    const extraction = task.extraction_snapshot;
    const modules = stringArray(extraction.functional_modules);
    setDraft({
      source,
      task,
      title: String(
        extraction.requirement_summary ?? source.raw_text.slice(0, 80),
      ),
      module: modules[0] ?? "未分类",
      description: String(
        extraction.requirement_description ?? source.raw_text,
      ),
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
            target_requirement_key: null,
            expected_requirement_id: null,
            expected_current_version: null,
            operations: [operation],
            comment: "用户确认保留该需求",
          }),
        },
        session,
      );
      setDraft(null);
      void message.success("需求已保留并生成正式版本");
      if (conversationKey) {
        const refreshed = await Promise.all([
          loadMessages(conversationKey, false, false),
          loadConversationList(false, false),
        ]);
        if (refreshed.includes(false)) {
          void message.warning("审核已提交，但刷新最新数据失败；请稍后重试刷新。");
        }
      }
    } catch (caught) {
      setError(caught);
      void message.error("审核请求失败，需求状态未确认更新。");
    } finally {
      setSubmitting(false);
    }
  }

  async function reject(task: ReviewTask) {
    setSubmitting(true);
    try {
      const updatedReview = await api<ReviewTask>(
        `/api/v1/review-tasks/${task.id}/reject`,
        {
          method: "POST",
          body: JSON.stringify({
            comment: "用户在需求对话中选择不保留",
          }),
        },
        session,
      );
      setMessages((current) => applyReviewTaskUpdate(current, updatedReview));
      void message.success("该需求已标记为不保留");
      if (conversationKey) {
        const refreshed = await Promise.all([
          loadMessages(conversationKey, false, false),
          loadConversationList(false, false),
        ]);
        if (refreshed.includes(false)) {
          void message.warning("审核已提交，但刷新最新数据失败；请稍后重试刷新。");
        }
      }
    } catch (caught) {
      setError(caught);
      void message.error("审核请求失败，需求状态未确认更新。");
    } finally {
      setSubmitting(false);
    }
  }

  async function retry(item: ConversationMessage) {
    if (!conversationKey) return;
    try {
      await api(
        `/api/v1/conversations/${encodeURIComponent(conversationKey)}/messages/${encodeURIComponent(item.message_key)}/reanalyze`,
        { method: "POST" },
        session,
      );
      setMessages((current) =>
        current.map((messageItem) =>
          messageItem.message_key === item.message_key && messageItem.source
            ? {
                ...messageItem,
                source: {
                  ...messageItem.source,
                  processing_status: "parsing",
                },
              }
            : messageItem,
        ),
      );
      void message.success("已重新提交分析");
      await loadMessages(conversationKey);
    } catch (caught) {
      setError(caught);
    }
  }

  function openRename(conversation: Conversation) {
    setRenameTarget(conversation);
    setRenameTitle(conversation.title);
  }

  async function renameConversation() {
    if (!renameTarget || !renameTitle.trim()) {
      void message.error("会话标题不能为空");
      return;
    }
    setRenaming(true);
    try {
      const updated = await api<Conversation>(
        `/api/v1/conversations/${encodeURIComponent(renameTarget.conversation_key)}`,
        {
          method: "PATCH",
          body: JSON.stringify({ title: renameTitle.trim() }),
        },
        session,
      );
      listRequestGeneration.current += 1;
      setConversations((current) =>
        sortConversations(
          current.map((item) =>
            item.conversation_key === updated.conversation_key ? updated : item,
          ),
        ),
      );
      setRenameTarget(null);
      void message.success("会话已重命名");
    } catch (caught) {
      setError(caught);
    } finally {
      setRenaming(false);
    }
  }

  async function deleteConversation(target: Conversation) {
    setDeletingKey(target.conversation_key);
    try {
      await api<void>(
        `/api/v1/conversations/${encodeURIComponent(target.conversation_key)}`,
        { method: "DELETE" },
        session,
      );
      listRequestGeneration.current += 1;
      const index = conversations.findIndex(
        (item) => item.conversation_key === target.conversation_key,
      );
      const remaining = conversations.filter(
        (item) => item.conversation_key !== target.conversation_key,
      );
      setConversations(remaining);
      void message.success("会话已删除");
      if (target.conversation_key === conversationKey) {
        messageRequestGeneration.current += 1;
        const next = remaining[Math.min(Math.max(index, 0), remaining.length - 1)];
        if (next) {
          navigate(`/chat/${next.conversation_key}`, { replace: true });
        } else {
          await createConversation(true);
        }
      }
    } catch (caught) {
      setError(caught);
    } finally {
      setDeletingKey(null);
    }
  }

  const sidebar = (
    <ConversationSidebar
      conversations={conversations}
      activeKey={conversationKey}
      loading={loadingConversations}
      creating={creating}
      deletingKey={deletingKey}
      onCreate={() => void createConversation()}
      onSelect={(key) => {
        setDrawerOpen(false);
        if (key !== conversationKey) navigate(`/chat/${key}`);
      }}
      onRename={openRename}
      onDelete={(conversation) => void deleteConversation(conversation)}
    />
  );

  return (
    <div className="conversation-page">
      {!isMobile && <aside className="conversation-sidebar">{sidebar}</aside>}
      {isMobile && (
        <Drawer
          open={drawerOpen}
          title="需求会话"
          placement="left"
          width="min(88vw, 340px)"
          className="conversation-drawer"
          onClose={() => setDrawerOpen(false)}
        >
          {sidebar}
        </Drawer>
      )}

      <section className="conversation-main">
        <div className="conversation-heading">
          <Space align="start">
            {isMobile && (
              <Tooltip title="打开会话列表">
                <Button
                  type="text"
                  icon={<MenuOutlined />}
                  aria-label="打开会话列表"
                  onClick={() => setDrawerOpen(true)}
                />
              </Tooltip>
            )}
            <div>
              <Typography.Title level={3}>
                {activeConversation?.title ?? "需求对话"}
              </Typography.Title>
              {activeConversation?.summary ? (
                <Typography.Text type="secondary" ellipsis>
                  {activeConversation.summary}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  描述你的想法，AI 会结合本会话记忆分析需求。
                </Typography.Text>
              )}
              {!!activeConversation?.memory_revision && (
                <Typography.Text className="conversation-memory-meta" type="secondary">
                  记忆版本 {activeConversation.memory_revision} · 已覆盖至消息{" "}
                  {activeConversation.memory_covered_sequence}
                </Typography.Text>
              )}
            </div>
          </Space>
          {hasActiveTurn && (
            <Tag icon={<LoadingOutlined />} color="processing">
              AI 分析中
            </Tag>
          )}
          {!hasActiveTurn && activeConversation && (
            <Popconfirm
              title="清空模型上下文？"
              description="不会删除需求、附件、版本、审核或审计记录。"
              onConfirm={() => void clearModelContext()}
              okText="清空"
              cancelText="取消"
            >
              <Button size="small">清空模型上下文</Button>
            </Popconfirm>
          )}
        </div>

        {error !== null && (
          <div className="conversation-error">
            <ErrorBlock error={error} />
          </div>
        )}
        <div className="conversation-stream" ref={conversationStreamRef}>
          {loadingMessages ? (
            <div className="state-block">
              <Spin size="large" />
            </div>
          ) : messages.length === 0 ? (
            <EmptyBlock description="这个会话还没有需求，先在下方描述一个想法吧" />
          ) : (
            messages.map((item) => item.source ? (
              <ConversationTurn key={item.message_key} source={item.source}
                review={item.review_task ?? undefined} onRetain={openDraft}
                onReject={(task) => void reject(task)} onRetry={() => void retry(item)}
                onAdvanced={(task) => navigate(`/reviews/${task.id}`)}
                onOpenRequirement={(task) => {
                  if (task.target_requirement_id) navigate(`/requirements/${task.target_requirement_id}`);
                }} />
            ) : (
              <ChatConversationTurn key={item.message_key} message={item}
                onReference={(reference) => navigate(
                  reference.type === "source"
                    ? `/sources/${encodeURIComponent(String(reference.id))}`
                    : `/requirements?requirement_key=${encodeURIComponent(String(reference.id))}`,
                )} />
            ))
          )}
        </div>

        <Card className="conversation-composer">
          <Input.TextArea
            value={text}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder="请输入需求，或查询历史需求、来源和会话内容"
            aria-label="会话消息"
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
                ref={fileInputRef}
                type="file"
                aria-label="添加附件"
                accept=".pdf,.docx,.png,.jpg,.jpeg"
                onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              />
            </label>
            <Space>
              {file && (
                <Button
                  type="text"
                  onClick={() => {
                    setFile(null);
                    if (fileInputRef.current) fileInputRef.current.value = "";
                  }}
                >
                  移除附件
                </Button>
              )}
              <Button
                type="primary"
                icon={<SendOutlined />}
                loading={sending}
                disabled={!conversationKey}
                onClick={() => void sendRequirement()}
              >
                发送
              </Button>
            </Space>
          </div>
        </Card>
      </section>

      <Modal
        open={renameTarget !== null}
        title="重命名会话"
        okText="保存"
        cancelText="取消"
        confirmLoading={renaming}
        onOk={() => void renameConversation()}
        onCancel={() => setRenameTarget(null)}
      >
        <Input
          value={renameTitle}
          maxLength={255}
          showCount
          autoFocus
          aria-label="会话标题"
          onChange={(event) => setRenameTitle(event.target.value)}
          onPressEnter={() => void renameConversation()}
        />
      </Modal>

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
                onChange={(event) =>
                  setDraft({ ...draft, title: event.target.value })
                }
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
                  String(option?.value ?? "")
                    .toLowerCase()
                    .includes(input.toLowerCase())
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
                onChange={(event) =>
                  setDraft({ ...draft, criteria: event.target.value })
                }
              />
            </label>
          </Space>
        )}
      </Modal>
    </div>
  );
}

function ConversationSidebar({
  conversations,
  activeKey,
  loading,
  creating,
  deletingKey,
  onCreate,
  onSelect,
  onRename,
  onDelete,
}: {
  conversations: Conversation[];
  activeKey?: string;
  loading: boolean;
  creating: boolean;
  deletingKey: string | null;
  onCreate: () => void;
  onSelect: (key: string) => void;
  onRename: (conversation: Conversation) => void;
  onDelete: (conversation: Conversation) => void;
}) {
  return (
    <div className="conversation-sidebar-inner">
      <div className="conversation-sidebar-header">
        <Typography.Title level={5}>需求会话</Typography.Title>
        <Tooltip title="新建会话">
          <Button
            type="primary"
            icon={<PlusOutlined />}
            loading={creating}
            aria-label="新建会话"
            onClick={onCreate}
          />
        </Tooltip>
      </div>
      <div className="conversation-list">
        {loading && conversations.length === 0 ? (
          <div className="conversation-list-loading">
            <Spin />
          </div>
        ) : conversations.length === 0 ? (
          <EmptyBlock description="暂无会话" />
        ) : (
          conversations.map((conversation) => {
            const selected = conversation.conversation_key === activeKey;
            return (
              <div
                key={conversation.conversation_key}
                className={`conversation-list-item${selected ? " selected" : ""}`}
              >
                <button
                  type="button"
                  className="conversation-list-select"
                  aria-current={selected ? "page" : undefined}
                  onClick={() => onSelect(conversation.conversation_key)}
                >
                  <MessageOutlined />
                  <span className="conversation-list-copy">
                    <span className="conversation-list-title">
                      {conversation.title}
                    </span>
                    <span className="conversation-list-detail">
                      {conversation.summary ||
                        `更新于 ${formatDate(conversation.updated_at)}`}
                    </span>
                    {!!conversation.memory_revision && (
                      <span className="conversation-list-memory">
                        记忆 v{conversation.memory_revision}
                      </span>
                    )}
                  </span>
                </button>
                <div className="conversation-list-actions">
                  <Tooltip title="重命名">
                    <Button
                      type="text"
                      size="small"
                      icon={<EditOutlined />}
                      aria-label={`重命名会话：${conversation.title}`}
                      onClick={() => onRename(conversation)}
                    />
                  </Tooltip>
                  <Popconfirm
                    title="删除这个会话？"
                    description="会话及其记忆将从列表中移除，此操作不可撤销。"
                    okText="删除"
                    cancelText="取消"
                    okButtonProps={{ danger: true }}
                    onConfirm={() => onDelete(conversation)}
                  >
                    <Tooltip title="删除">
                      <Button
                        type="text"
                        danger
                        size="small"
                        icon={<DeleteOutlined />}
                        loading={deletingKey === conversation.conversation_key}
                        aria-label={`删除会话：${conversation.title}`}
                      />
                    </Tooltip>
                  </Popconfirm>
                </div>
              </div>
            );
          })
        )}
      </div>
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
        <div className="chat-avatar">
          <UserOutlined />
        </div>
        <div className="chat-bubble user-bubble">
          <Typography.Paragraph>
            {source.raw_text || "请分析附件中的需求"}
          </Typography.Paragraph>
          {source.attachments.map((attachment) => (
            <Tag icon={<FileOutlined />} key={attachment.id}>
              {attachment.file_name}
            </Tag>
          ))}
          <div className="chat-meta">
            {source.source_key} · {formatDate(source.received_at)}
          </div>
        </div>
      </div>
      <div className="chat-row chat-row-assistant">
        <div className="chat-avatar assistant-avatar">
          <RobotOutlined />
        </div>
        <div className="chat-bubble assistant-bubble">
          {failed ? (
            <>
              <Alert
                type="error"
                showIcon
                message="分析未完成"
                description={`当前状态：${source.processing_status}。原始输入已经安全保存。`}
              />
              <Button className="chat-action" onClick={onRetry}>
                重新分析
              </Button>
            </>
          ) : !review ? (
            <Space>
              <Spin size="small" />
              <span>
                {progressText[source.processing_status] ??
                  source.processing_status}
              </span>
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

function ChatConversationTurn({
  message,
  onReference,
}: {
  message: ConversationMessage;
  onReference: (reference: Record<string, unknown>) => void;
}) {
  const assistant = message.role === "assistant";
  return (
    <div className={`chat-row ${assistant ? "chat-row-assistant" : "chat-row-user"}`}>
      <div className={`chat-avatar${assistant ? " assistant-avatar" : ""}`}>
        {assistant ? <RobotOutlined /> : <UserOutlined />}
      </div>
      <div className={`chat-bubble ${assistant ? "assistant-bubble" : "user-bubble"}`}>
        <Typography.Paragraph style={{ whiteSpace: "pre-wrap" }}>
          {message.content}
        </Typography.Paragraph>
        {!!message.tool_calls.length && (
          <Typography.Text type="secondary">
            已调用：{message.tool_calls.map((item) => item.tool_name).join("、")}
          </Typography.Text>
        )}
        {!!message.references.length && (
          <Space wrap style={{ marginTop: 8 }}>
            {message.references.map((reference) => (
              <Button
                key={`${String(reference.type)}-${String(reference.id)}`}
                size="small"
                onClick={() => onReference(reference)}
              >
                {reference.type === "source" ? "来源" : "需求"}：{String(reference.id)}
              </Button>
            ))}
          </Space>
        )}
        <div className="chat-meta">{formatDate(message.created_at)}</div>
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
          <Button onClick={() => onAdvanced(review)}>
            合并已有需求 / 高级审核
          </Button>
        </Space>
      ) : review.review_status === "approved" ? (
        <Button type="link" onClick={() => onOpenRequirement(review)}>
          已保留，查看正式需求
        </Button>
      ) : (
        <Typography.Text type="secondary">
          {review.review_status === "rejected"
            ? "你已选择不保留该需求。"
            : "该需求已退回处理。"}
        </Typography.Text>
      )}
    </>
  );
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}
