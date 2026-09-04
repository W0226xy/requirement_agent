import { RedoOutlined } from "@ant-design/icons";
import { Button, Card, Collapse, Segmented, Space, Table, Typography, message } from "antd";
import { useCallback, useEffect, useState } from "react";

import { api, queryString } from "../api";
import { ErrorBlock, JsonView, PageTitle, StatusTag, formatDate } from "../components";
import { useSession } from "../session";
import type { AnalysisResult, PageResponse } from "../types";

export function AnalysisPage() {
  const session = useSession();
  const [mode, setMode] = useState<"all" | "failed">("all");
  const [data, setData] = useState<PageResponse<AnalysisResult> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(
        await api<PageResponse<AnalysisResult>>(
          `${mode === "failed" ? "/api/v1/failed-jobs" : "/api/v1/analysis-results"}${queryString({
            page: 1,
            page_size: 100,
          })}`,
        ),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, [mode]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageTitle title="AI 调用与失败任务" subtitle="查看模型、提示词版本、耗时、输出和错误；密钥不会记录" />
      <Card>
        <Space className="filters">
          <Segmented
            value={mode}
            options={[
              { value: "all", label: "全部调用" },
              { value: "failed", label: "失败任务" },
            ]}
            onChange={(value) => setMode(value as "all" | "failed")}
          />
          <Button onClick={() => void load()}>刷新</Button>
        </Space>
        {error !== null && <ErrorBlock error={error} />}
        <Table
          rowKey="id"
          loading={loading}
          dataSource={data?.items ?? []}
          pagination={{ pageSize: 20 }}
          expandable={{
            expandedRowRender: (record) => (
              <Collapse
                defaultActiveKey={record.error_message ? ["error"] : []}
                items={[
                  {
                    key: "input",
                    label: "输入快照",
                    children: <JsonView value={record.input_snapshot} />,
                  },
                  {
                    key: "output",
                    label: "输出结果",
                    children: <JsonView value={record.result_json ?? record.raw_output} />,
                  },
                  ...(record.error_message
                    ? [{
                        key: "error",
                        label: "错误信息",
                        children: <Typography.Text type="danger">{record.error_message}</Typography.Text>,
                      }]
                    : []),
                ]}
              />
            ),
          }}
          columns={[
            { title: "ID", dataIndex: "id", width: 80 },
            { title: "来源", dataIndex: "source_record_id", render: (value: number) => `#${value}` },
            { title: "类型", dataIndex: "analysis_type", render: (value: string) => <StatusTag value={value} /> },
            { title: "模型", dataIndex: "model_name" },
            { title: "提示词", dataIndex: "prompt_version" },
            { title: "耗时", dataIndex: "duration_ms", render: (value: number) => `${value} ms` },
            {
              title: "结果",
              render: (_: unknown, record: AnalysisResult) => (
                <StatusTag value={record.error_message ? "failed" : "success"} />
              ),
            },
            { title: "时间", dataIndex: "created_at", render: formatDate },
            {
              title: "操作",
              render: (_: unknown, record: AnalysisResult) =>
                record.error_message ? (
                  <Button
                    type="link"
                    icon={<RedoOutlined />}
                    onClick={async () => {
                      try {
                        await api(
                          `/api/v1/failed-jobs/${record.id}/retry`,
                          { method: "POST" },
                          session,
                        );
                        void message.success("已加入重新分析队列");
                      } catch (caught) {
                        setError(caught);
                      }
                    }}
                  >
                    重试
                  </Button>
                ) : null,
            },
          ]}
        />
      </Card>
    </>
  );
}
