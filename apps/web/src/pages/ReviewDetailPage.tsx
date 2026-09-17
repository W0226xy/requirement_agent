import {
  ArrowLeftOutlined,
  CheckOutlined,
  DeleteOutlined,
  PlusOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Card,
  Col,
  Collapse,
  Descriptions,
  Divider,
  Form,
  Input,
  List,
  message,
  Modal,
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
import type { PageResponse, Requirement, ReviewTask, SourceRecord } from "../types";
import {
  applyTargetFeature,
  buildApprovalRequest,
  buildOperations,
  changeOperationOptions,
  newOperationFormValue,
  proposedOperationsToFormValues,
  recalculateSuggestedDraft,
  type DraftField,
  type OperationFormValue,
} from "./reviewOperationForm";

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
  const [operationForms, setOperationForms] = useState<OperationFormValue[]>([]);
  const [operationErrors, setOperationErrors] = useState<Record<string, string>>({});
  const [targetRequirement, setTargetRequirement] = useState<Requirement | null>(null);
  const [targetChanged, setTargetChanged] = useState(false);
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
        setOperationForms(proposedOperationsToFormValues(
          review.analysis_snapshot.proposed_operations,
          review.extraction_snapshot,
        ));
      })
      .catch(setError);
  }, [id]);

  const generatedOperations = useMemo(
    () => buildOperations(operationForms, source?.id ?? 0),
    [operationForms, source?.id],
  );

  if (error) return <ErrorBlock error={error} />;
  if (!task || !source) return <LoadingBlock />;
  const pending = task.review_status === "pending";
  const sourceRecordId = source.id;

  async function approve() {
    const built = buildOperations(operationForms, sourceRecordId);
    setOperationErrors(built.errors);
    if (!built.operations) {
      void message.error(built.errors.operations ?? "请检查变更操作中的必填项");
      return;
    }
    const target = requirements.find((item) => item.id === targetId);
    if (
      decision === "merge" &&
      (!target || target.current_version_number === null)
    ) {
      void message.error("请选择有当前版本的目标需求");
      return;
    }
    setSubmitting(true);
    try {
      const result = await api<{ requirement_id: number }>(
        `/api/v1/review-tasks/${id}/approve`,
        {
          method: "POST",
          body: JSON.stringify(buildApprovalRequest(decision, title, target, built.operations, comment)),
        },
        session,
      );
      void message.success("审核通过，正式版本已生成");
      navigate(`/requirements/${result.requirement_id}`);
    } catch (caught) {
      void message.error(approvalErrorMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  async function selectTarget(nextTargetId: number) {
    setTargetId(nextTargetId);
    setTargetRequirement(null);
    setTargetChanged(true);
    setOperationForms((current) => current.map((item) => (
      item.operation === "add" ? item : { ...item, featureKey: null }
    )));
    try {
      setTargetRequirement(await api<Requirement>(`/api/v1/requirements/${nextTargetId}`));
    } catch (caught) {
      setError(caught);
    }
  }

  function updateOperation(
    id: string,
    patch: Partial<OperationFormValue>,
    dirtyFields: DraftField[] = [],
  ) {
    setOperationForms((current) => current.map((item) => item.id === id ? {
      ...item, ...patch,
      dirty: dirtyFields.length ? {
        ...item.dirty,
        ...Object.fromEntries(dirtyFields.map((field) => [field, true])),
      } : item.dirty,
    } : item));
    setOperationErrors((current) => {
      const next = { ...current };
      for (const key of Object.keys(patch)) delete next[`${id}.${key}`];
      return next;
    });
  }

  function changeOperation(item: OperationFormValue, operation: OperationFormValue["operation"]) {
    const featureKey = operation === "add" ? null : item.featureKey;
    const features = decision === "merge" ? targetRequirement?.features ?? [] : [];
    updateOperation(
      item.id,
      recalculateSuggestedDraft(
        { ...item, operation, featureKey }, features, task?.extraction_snapshot ?? {},
      ),
    );
  }

  function refillFromSuggestion(item: OperationFormValue) {
    const features = decision === "merge" ? targetRequirement?.features ?? [] : [];
    const refill = () => updateOperation(
      item.id,
      recalculateSuggestedDraft(item, features, task?.extraction_snapshot ?? {}, true),
    );
    if (Object.values(item.dirty).some(Boolean)) {
      Modal.confirm({
        title: "按 AI 建议重新填充？",
        content: "这会覆盖当前手动编辑的功能模块、标题、描述和验收标准。",
        okText: "覆盖并重新填充",
        cancelText: "取消",
        onOk: refill,
      });
      return;
    }
    refill();
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
            onChange={(value) => void selectTarget(value)}
          />
        )}
        {decision === "merge" && targetChanged && (
          <Alert
            type="warning"
            showIcon
            message="目标需求已切换"
            description="已清除修改、删除、恢复操作的目标功能。请重新选择目标功能并检查表单内容。"
          />
        )}
        <Typography.Text strong>变更操作</Typography.Text>
        {operationErrors.operations && <Alert type="error" showIcon message={operationErrors.operations} />}
        <Space direction="vertical" size="middle" className="operation-forms">
          {operationForms.map((item, index) => {
            const needsTarget = item.operation !== "add";
            const hasContent = item.operation !== "delete";
            const selectableFeatures = decision === "merge" ? targetRequirement?.features ?? [] : [];
            return (
              <Card
                size="small"
                key={item.id}
                title={`变更项 ${index + 1}`}
                extra={pending && <Button danger type="text" icon={<DeleteOutlined />} onClick={() => setOperationForms((current) => current.filter((value) => value.id !== item.id))}>删除当前变更项</Button>}
              >
                <Form layout="vertical">
                  <Row gutter={12}>
                    <Col xs={24} md={8}><Form.Item label="变更类型"><Select disabled={!pending} value={item.operation} options={changeOperationOptions} onChange={(operation) => changeOperation(item, operation)} /></Form.Item></Col>
                    {needsTarget && <Col xs={24} md={16}><Form.Item label="目标功能" validateStatus={operationErrors[`${item.id}.featureKey`] ? "error" : undefined} help={operationErrors[`${item.id}.featureKey`]}><Select disabled={!pending || decision !== "merge" || !targetRequirement} value={item.featureKey ?? undefined} placeholder={decision === "merge" ? "请选择已有功能" : "创建新需求时不能修改或删除已有功能"} options={selectableFeatures.map((feature) => ({ value: feature.feature_key, label: `${feature.feature_key} · ${feature.feature_title}` }))} onChange={(featureKey) => updateOperation(item.id, applyTargetFeature(item, featureKey, selectableFeatures, task.extraction_snapshot))} /></Form.Item></Col>}
                  </Row>
                  {hasContent && <>
                    <Button disabled={!pending} size="small" onClick={() => refillFromSuggestion(item)}>按 AI 建议重新填充</Button>
                    <Form.Item label="功能模块" validateStatus={operationErrors[`${item.id}.module`] ? "error" : undefined} help={operationErrors[`${item.id}.module`]}><Input disabled={!pending} value={item.module} onChange={(event) => updateOperation(item.id, { module: event.target.value }, ["module"])} /></Form.Item>
                    <Form.Item label="功能标题" validateStatus={operationErrors[`${item.id}.featureTitle`] ? "error" : undefined} help={operationErrors[`${item.id}.featureTitle`]}><Input disabled={!pending} value={item.featureTitle} onChange={(event) => updateOperation(item.id, { featureTitle: event.target.value }, ["featureTitle"])} /></Form.Item>
                    <Form.Item label="功能描述" validateStatus={operationErrors[`${item.id}.featureDescription`] ? "error" : undefined} help={operationErrors[`${item.id}.featureDescription`]}><Input.TextArea disabled={!pending} value={item.featureDescription} autoSize={{ minRows: 2 }} onChange={(event) => updateOperation(item.id, { featureDescription: event.target.value }, ["featureDescription"])} /></Form.Item>
                    <Form.Item label="验收标准"><Space direction="vertical" className="acceptance-list">{item.acceptanceCriteria.map((criterion, criterionIndex) => <Space key={criterionIndex}><Input disabled={!pending} value={criterion} onChange={(event) => updateOperation(item.id, { acceptanceCriteria: item.acceptanceCriteria.map((value, valueIndex) => valueIndex === criterionIndex ? event.target.value : value) }, ["acceptanceCriteria"])} /><Button disabled={!pending} danger icon={<DeleteOutlined />} onClick={() => updateOperation(item.id, { acceptanceCriteria: item.acceptanceCriteria.filter((_, valueIndex) => valueIndex !== criterionIndex) }, ["acceptanceCriteria"])} /></Space>)}<Button disabled={!pending} icon={<PlusOutlined />} onClick={() => updateOperation(item.id, { acceptanceCriteria: [...item.acceptanceCriteria, ""] }, ["acceptanceCriteria"])}>新增验收标准</Button></Space></Form.Item>
                  </>}
                  <Form.Item label="变更原因" validateStatus={operationErrors[`${item.id}.reason`] ? "error" : undefined} help={operationErrors[`${item.id}.reason`]}><Input.TextArea disabled={!pending} value={item.reason} autoSize={{ minRows: 2 }} onChange={(event) => updateOperation(item.id, { reason: event.target.value })} /></Form.Item>
                </Form>
              </Card>
            );
          })}
        </Space>
        {pending && <Button icon={<PlusOutlined />} onClick={() => setOperationForms((current) => [...current, newOperationFormValue(task.extraction_snapshot)])}>新增变更项</Button>}
        <Collapse items={[{ key: "final-json", label: "查看最终 JSON（高级/调试）", children: <Input.TextArea readOnly value={JSON.stringify(generatedOperations.operations ?? [], null, 2)} autoSize={{ minRows: 6, maxRows: 20 }} /> }]} />
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

function approvalErrorMessage(error: unknown): string {
  const detail = error instanceof Error ? error.message : "提交失败，请稍后重试";
  if (detail.includes("target requirement changed")) return "目标需求版本已变化，请刷新后重新选择目标需求。";
  if (detail.includes("selected requirement does not match")) return "目标需求信息已变化，请重新选择后提交。";
  if (detail.includes("merge requires")) return "合并目标信息不完整，请重新选择目标需求。";
  if (detail.includes("all operations must reference")) return "变更操作来源不正确，请刷新审核任务后重试。";
  return `提交失败：${detail}`;
}
