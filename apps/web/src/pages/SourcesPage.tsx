import { Button, Card, Input, Select, Space, Table } from "antd";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, queryString } from "../api";
import { ErrorBlock, PageTitle, StatusTag, formatDate } from "../components";
import type { PageResponse, SourceRecord } from "../types";

export function SourcesPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<PageResponse<SourceRecord> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [channel, setChannel] = useState("");
  const [status, setStatus] = useState("");
  const [submitter, setSubmitter] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(
        await api<PageResponse<SourceRecord>>(
          `/api/v1/source-records${queryString({
            page: 1,
            page_size: 100,
            channel_type: channel,
            processing_status: status,
            submitter_id: submitter,
          })}`,
        ),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, [channel, status, submitter]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageTitle title="原始输入" subtitle="原始资料只新增、不覆盖，失败后仍可追溯" />
      <Card>
        <Space className="filters" wrap>
          <Select
            allowClear
            placeholder="渠道"
            style={{ width: 160 }}
            options={["web_form", "document", "image", "feishu"].map((value) => ({
              value,
              label: value,
            }))}
            onChange={(value) => setChannel(value ?? "")}
          />
          <Select
            allowClear
            placeholder="处理状态"
            style={{ width: 180 }}
            options={[
              "received", "parsing", "analyzing", "pending_review", "versioned",
              "parse_failed", "extraction_failed", "analysis_failed", "version_failed",
            ].map((value) => ({ value, label: value }))}
            onChange={(value) => setStatus(value ?? "")}
          />
          <Input.Search
            allowClear
            placeholder="输入人 ID"
            style={{ width: 220 }}
            onSearch={setSubmitter}
          />
          <Button onClick={() => void load()}>刷新</Button>
        </Space>
        {error !== null && <ErrorBlock error={error} />}
        <Table
          rowKey="id"
          loading={loading}
          dataSource={data?.items ?? []}
          pagination={{ pageSize: 20 }}
          columns={[
            { title: "来源编号", dataIndex: "source_key", width: 160 },
            { title: "渠道", dataIndex: "channel_type", render: (value: string) => <StatusTag value={value} /> },
            { title: "输入人", dataIndex: "submitter_name" },
            {
              title: "原始文本",
              dataIndex: "raw_text",
              ellipsis: true,
            },
            {
              title: "处理状态",
              dataIndex: "processing_status",
              render: (value: string) => <StatusTag value={value} />,
            },
            { title: "接收时间", dataIndex: "received_at", render: formatDate },
            {
              title: "操作",
              render: (_: unknown, record: SourceRecord) => (
                <Button type="link" onClick={() => navigate(`/sources/${record.id}`)}>查看</Button>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
