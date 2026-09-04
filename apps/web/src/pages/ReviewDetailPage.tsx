import { ArrowLeftOutlined, CheckOutlined, RedoOutlined } from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Divider,
  Input,
  List,
  message,
  Radio,
  Row,
  Select,
  Space,
  Tag,
  Typography,
} from "antd";
import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import {
  EmptyBlock,
  ErrorBlock,
  JsonView,
  LoadingBlock,
  PageTitle,
  StatusTag,
  formatDate,
} from "../components";
import { useSession } from "../session";
import type {
  PageResponse,
  ProposedOperation,
  Requirement,
  ReviewTask,
  SourceRecord,
} from "../types";

export function ReviewDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const session = useSession();
  const [task, setTask] = useState<ReviewTask | null>(null);
  const [source, setSource] = useState<SourceRecord | null>(null);
  const [requirements, setRequirements] = useState<Requirement[]>([]);
  const [decision, setDecision] = useState<"create" | "merge">("create");
  const [title, setTitle] = useState("");
  const [targetId, setTargetId] = useState<number | undefined>();
  const [operationsText, setOperationsText] = useState("[]");
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api<ReviewTask>(`/api/v1/review-tasks/${id}`)
      .then(async (review) => {
        const [rawSource, requirementPage] = await Promise.all([
          api<SourceRecord>(`/api/v1/source-records/${review.source_record_id}`),
          api<PageResponse<Requirement>>("/api/v1/requirements?page=1&page_size=100"),
        ]);
        setTask(review);
        setSource(rawSource);
        setRequirements(requirementPage.items);
        setTitle(String(review.extraction_snapshot.requirement_summary ?? ""));
        setOperationsText(
          JSON.stringify(review.analysis_snapshot.proposed_operations ?? [], null, 2),
        );
      })
      .catch(setError);
  }, [id]);

  const operations = useMemo(() => {
    try {
      const parsed = JSON.parse(operationsText) as unknown;
      return Array.isArray(parsed) ? (parsed as ProposedOperation[]) : null;
    } catch {
      return null;
    }
  }, [operationsText]);

  if (error) return <ErrorBlock error={error} />;
  if (!task || !source) return <LoadingBlock />;
  const pending = task.review_status === "pending";

  async function approve() {
    if (!operations?.length) {
      void message.error("变更操作必须是非空 JSON 数组");
      return;
    }
    setSubmitting(true);
    try {
      const result = await api<{ requirement_id: number }>(
        `/api/v1/review-tasks/${id}/approve`,
        {
          method: "POST",
          body: JSON.stringify({
            decision,
            title: decision === "create" ? title : null,
            target_requirement_id: decision === "merge" ? targetId : null,
            operations,
            comment: comment || null,
          }),
        },
        session,
      );
      void message.success("审核通过，正式版本已生成");
      navigate(`/requirements/${result.requirement_id}`);
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  }

  async function action(name: "reject" | "return" | "reanalyze") {
    if (name !== "reanalyze" && !comment.trim()) {
      void message.error("请填写审核意见");
      return;
    }
    setSubmitting(true);
    try {
      await api(
        `/api/v1/review-tasks/${id}/${name}`,
        {
          method: "POST",
          body: name === "reanalyze" ? undefined : JSON.stringify({ comment }),
        },
        session,
      );
      void message.success(name === "reanalyze" ? "已提交重新分析" : "审核状态已更新");
      navigate("/reviews");
    } catch (caught) {
      setError(caught);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <PageTitle
        title={`审核任务 #${task.id}`}
        subtitle={`创建于 ${formatDate(task.created_at)}`}
        extra={
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/reviews")}>
            返回审核中心
          </Button>
        }
      />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={12}>
          <Card title="原始输入">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="来源编号">{source.source_key}</Descriptions.Item>
              <Descriptions.Item label="渠道"><StatusTag value={source.channel_type} /></Descriptions.Item>
              <Descriptions.Item label="输入人">{source.submitter_name}</Descriptions.Item>
              <Descriptions.Item label="接收时间">{formatDate(source.received_at)}</Descriptions.Item>
            </Descriptions>
            <Typography.Paragraph className="source-text">{source.raw_text || "无正文"}</Typography.Paragraph>
            <Collapse
              items={source.attachments.map((attachment) => ({
                key: attachment.id,
                label: `${attachment.file_name} · ${attachment.parse_status}`,
                children: (
                  <Typography.Paragraph className="source-text">
                    {attachment.parsed_text || attachment.ocr_text || attachment.error_message || "无解析文本"}
                  </Typography.Paragraph>
                ),
              }))}
            />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="AI 结构化提取">
            <Descriptions column={1} size="small">
              <Descriptions.Item label="需求摘要">
                {String(task.extraction_snapshot.requirement_summary ?? "-")}
              </Descriptions.Item>
              <Descriptions.Item label="需求描述">
                {String(task.extraction_snapshot.requirement_description ?? "-")}
              </Descriptions.Item>
            </Descriptions>
            <JsonView value={task.extraction_snapshot} />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="相似需求与冲突证据">
            <Space wrap className="summary-tags">
              <StatusTag value={task.analysis_snapshot.conflict_status} />
              {(task.analysis_snapshot.conflicts ?? []).map((item, index) => (
                <Tag key={index} color="red">{String(item.requirement_id ?? "冲突")}</Tag>
              ))}
            </Space>
            {task.candidate_snapshot.length ? (
              <List
                dataSource={task.candidate_snapshot}
                renderItem={(candidate) => (
                  <List.Item>
                    <List.Item.Meta
                      title={`${String(candidate.requirement_key)} · ${String(candidate.title)}`}
                      description={`相似度 ${Math.round(Number(candidate.similarity_score) * 100)}% · ${String(candidate.matched_text ?? "")}`}
                    />
                  </List.Item>
                )}
              />
            ) : <EmptyBlock description="未检索到候选需求" />}
            <Collapse
              items={[
                { key: "conflicts", label: "冲突分析详情", children: <JsonView value={task.analysis_snapshot.conflicts ?? []} /> },
              ]}
            />
          </Card>
        </Col>
        <Col xs={24} xl={12}>
          <Card title="风险与待确认问题">
            <List
              dataSource={task.analysis_snapshot.risks ?? []}
              locale={{ emptyText: "无已识别风险" }}
              renderItem={(risk) => (
                <List.Item>
                  <List.Item.Meta
                    title={<Space><StatusTag value={String(risk.level)} />{String(risk.type)}</Space>}
                    description={`${String(risk.description)} · 缓解建议：${String(risk.mitigation)}`}
                  />
                </List.Item>
              )}
            />
            <Divider />
            <List
              header="待确认问题"
              dataSource={task.analysis_snapshot.clarification_questions ?? []}
              locale={{ emptyText: "无待确认问题" }}
              renderItem={(item) => <List.Item>{item}</List.Item>}
            />
          </Card>
        </Col>
      </Row>

      <Card title="人工决策与确定性变更" className="review-decision">
        {!pending && (
          <Alert
            type="info"
            showIcon
            message={`该任务已${task.review_status}`}
            description={task.review_comment || undefined}
          />
        )}
        <Radio.Group
          value={decision}
          disabled={!pending}
          onChange={(event) => setDecision(event.target.value as "create" | "merge")}
          options={[
            { value: "create", label: "创建新需求" },
            { value: "merge", label: "合并到已有需求" },
          ]}
        />
        {decision === "create" ? (
          <Input
            value={title}
            disabled={!pending}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="正式需求标题"
          />
        ) : (
          <Select
            showSearch
            value={targetId}
            disabled={!pending}
            placeholder="选择目标需求"
            optionFilterProp="label"
            options={requirements.map((item) => ({
              value: item.id,
              label: `${item.requirement_key} · ${item.title}`,
            }))}
            onChange={setTargetId}
          />
        )}
        <Typography.Text strong>变更操作（审核人可编辑）</Typography.Text>
        <Input.TextArea
          className="operation-editor"
          value={operationsText}
          disabled={!pending}
          status={operations === null ? "error" : undefined}
          autoSize={{ minRows: 12, maxRows: 28 }}
          onChange={(event) => setOperationsText(event.target.value)}
        />
        <Input.TextArea
          value={comment}
          disabled={!pending}
          placeholder="审核意见或版本变更原因"
          autoSize={{ minRows: 2, maxRows: 5 }}
          onChange={(event) => setComment(event.target.value)}
        />
        {pending && (
          <Space wrap>
            <Button type="primary" icon={<CheckOutlined />} loading={submitting} onClick={() => void approve()}>
              批准并生成版本
            </Button>
            <Button loading={submitting} onClick={() => void action("return")}>退回补充</Button>
            <Button danger loading={submitting} onClick={() => void action("reject")}>驳回</Button>
            <Button icon={<RedoOutlined />} loading={submitting} onClick={() => void action("reanalyze")}>
              重新分析
            </Button>
          </Space>
        )}
      </Card>
    </>
  );
}
