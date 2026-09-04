import { Badge, Button, Card, Select, Space, Table } from "antd";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, queryString } from "../api";
import { ErrorBlock, PageTitle, StatusTag, formatDate } from "../components";
import type { PageResponse, ReviewTask } from "../types";

export function ReviewsPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<PageResponse<ReviewTask> | null>(null);
  const [filter, setFilter] = useState("pending");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(
        await api<PageResponse<ReviewTask>>(
          `/api/v1/review-tasks${queryString({
            page: 1,
            page_size: 100,
            review_status: filter,
          })}`,
        ),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <>
      <PageTitle
        title="人工审核中心"
        subtitle="AI 结果只能作为建议，必须由审核人确认后才能生成正式版本"
        extra={<Badge count={filter === "pending" ? data?.total : 0} showZero color="#d89614" />}
      />
      <Card>
        <Space className="filters">
          <Select
            value={filter}
            style={{ width: 180 }}
            options={[
              { value: "pending", label: "待审核" },
              { value: "approved", label: "已批准" },
              { value: "returned", label: "已退回" },
              { value: "rejected", label: "已驳回" },
            ]}
            onChange={setFilter}
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
            { title: "任务", dataIndex: "id", width: 90, render: (id: number) => `#${id}` },
            {
              title: "需求摘要",
              render: (_: unknown, record: ReviewTask) =>
                String(record.extraction_snapshot.requirement_summary ?? "-"),
            },
            {
              title: "冲突结论",
              render: (_: unknown, record: ReviewTask) => (
                <StatusTag value={record.analysis_snapshot.conflict_status} />
              ),
            },
            {
              title: "审核状态",
              dataIndex: "review_status",
              render: (value: string) => <StatusTag value={value} />,
            },
            {
              title: "创建时间",
              dataIndex: "created_at",
              render: formatDate,
            },
            {
              title: "操作",
              render: (_: unknown, record: ReviewTask) => (
                <Button type="link" onClick={() => navigate(`/reviews/${record.id}`)}>
                  {record.review_status === "pending" ? "开始审核" : "查看"}
                </Button>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
