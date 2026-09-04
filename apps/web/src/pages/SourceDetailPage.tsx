import { ArrowLeftOutlined, RedoOutlined } from "@ant-design/icons";
import { Button, Card, Collapse, Descriptions, Space, Typography, message } from "antd";
import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorBlock, JsonView, LoadingBlock, PageTitle, StatusTag, formatDate } from "../components";
import type { AnalysisResult, PageResponse, SourceRecord } from "../types";

export function SourceDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [source, setSource] = useState<SourceRecord | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisResult[]>([]);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([
      api<SourceRecord>(`/api/v1/source-records/${id}`),
      api<PageResponse<AnalysisResult>>(`/api/v1/analysis-results?source_record_id=${id}&page_size=100`),
    ])
      .then(([record, calls]) => {
        setSource(record);
        setAnalysis(calls.items);
      })
      .catch(setError);
  }, [id]);

  if (error) return <ErrorBlock error={error} />;
  if (!source) return <LoadingBlock />;
  return (
    <>
      <PageTitle
        title={source.source_key}
        subtitle="不可变原始输入详情"
        extra={<Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/sources")}>返回列表</Button>}
      />
      <Card>
        <Descriptions column={{ xs: 1, md: 2 }}>
          <Descriptions.Item label="渠道"><StatusTag value={source.channel_type} /></Descriptions.Item>
          <Descriptions.Item label="状态"><StatusTag value={source.processing_status} /></Descriptions.Item>
          <Descriptions.Item label="输入人">{source.submitter_name} ({source.submitter_id})</Descriptions.Item>
          <Descriptions.Item label="接收时间">{formatDate(source.received_at)}</Descriptions.Item>
          <Descriptions.Item label="外部事件 ID">{source.external_event_id}</Descriptions.Item>
          <Descriptions.Item label="附件数">{source.attachments.length}</Descriptions.Item>
        </Descriptions>
        <Typography.Title level={4}>原始正文</Typography.Title>
        <Typography.Paragraph className="source-text">{source.raw_text || "无正文"}</Typography.Paragraph>
        <Collapse
          items={[
            {
              key: "metadata",
              label: "原始元数据",
              children: <JsonView value={source.raw_metadata} />,
            },
            ...source.attachments.map((attachment) => ({
              key: `attachment-${attachment.id}`,
              label: `${attachment.file_name} · ${attachment.parse_status}`,
              children: (
                <>
                  <Descriptions size="small" column={1}>
                    <Descriptions.Item label="类型">{attachment.file_type}</Descriptions.Item>
                    <Descriptions.Item label="对象路径">{attachment.storage_path}</Descriptions.Item>
                  </Descriptions>
                  <Typography.Paragraph className="source-text">
                    {attachment.parsed_text || attachment.ocr_text || attachment.error_message || "无解析结果"}
                  </Typography.Paragraph>
                </>
              ),
            })),
          ]}
        />
      </Card>
      <Card
        title="AI 调用链"
        className="section-card"
        extra={
          <Button
            icon={<RedoOutlined />}
            onClick={async () => {
              try {
                await api(`/api/v1/source-records/${id}/reanalyze`, { method: "POST" });
                void message.success("已加入重新分析队列");
              } catch (caught) {
                setError(caught);
              }
            }}
          >
            重新分析
          </Button>
        }
      >
        <Collapse
          items={analysis.map((item) => ({
            key: item.id,
            label: (
              <Space>
                <StatusTag value={item.error_message ? "failed" : item.analysis_type} />
                {item.model_name} · {item.duration_ms} ms · {formatDate(item.created_at)}
              </Space>
            ),
            children: (
              <>
                {item.error_message && <Typography.Text type="danger">{item.error_message}</Typography.Text>}
                <JsonView value={item.result_json ?? item.raw_output} />
              </>
            ),
          }))}
        />
      </Card>
    </>
  );
}
