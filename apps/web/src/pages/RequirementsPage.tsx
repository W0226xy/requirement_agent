import { EyeOutlined, ReloadOutlined } from "@ant-design/icons";
import { Button, Card, Input, Select, Space, Table, Tag } from "antd";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, queryString } from "../api";
import { ErrorBlock, PageTitle, StatusTag, formatDate } from "../components";
import type { PageResponse, Requirement } from "../types";

export function requirementListPath(
  page: number,
  keyword: string,
  status: string,
  module: string,
) {
  return `/api/v1/requirements${queryString({
    page,
    page_size: 20,
    keyword,
    status,
    module,
  })}`;
}

export function RequirementsPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<PageResponse<Requirement> | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [page, setPage] = useState(1);
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [module, setModule] = useState("");
  const [moduleOptions, setModuleOptions] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(
        await api<PageResponse<Requirement>>(
          requirementListPath(page, keyword, status, module),
        ),
      );
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, [keyword, module, page, status]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    void api<{ items: string[] }>("/api/v1/requirements/modules")
      .then((response) => setModuleOptions(response.items))
      .catch((caught) => setError(caught));
  }, []);

  return (
    <>
      <PageTitle
        title="需求管理"
        subtitle="只展示经过人工审核生成的正式需求"
        extra={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>}
      />
      <Card>
        <Space className="filters" wrap>
          <Input.Search
            allowClear
            placeholder="搜索需求标题"
            onSearch={(value) => {
              setPage(1);
              setKeyword(value);
            }}
            style={{ width: 280 }}
          />
          <Select
            value={module}
            style={{ width: 180 }}
            options={[
              { value: "", label: "全部模块" },
              ...moduleOptions.map((value) => ({ value, label: value })),
            ]}
            onChange={(value) => {
              setPage(1);
              setModule(value);
            }}
          />
          <Select
            allowClear
            placeholder="需求状态"
            style={{ width: 160 }}
            options={[
              { value: "active", label: "有效" },
              { value: "archived", label: "已归档" },
            ]}
            onChange={(value) => {
              setPage(1);
              setStatus(value ?? "");
            }}
          />
        </Space>
        {error !== null && <ErrorBlock error={error} />}
        <Table
          rowKey="id"
          loading={loading}
          dataSource={data?.items ?? []}
          pagination={{
            current: page,
            pageSize: 20,
            total: data?.total ?? 0,
            onChange: setPage,
          }}
          columns={[
            { title: "需求编号", dataIndex: "requirement_key", width: 160 },
            { title: "标题", dataIndex: "title" },
            {
              title: "模块",
              dataIndex: "functional_modules",
              render: (values: string[]) => values.map((value) => <Tag key={value}>{value}</Tag>),
            },
            {
              title: "状态",
              dataIndex: "status",
              width: 110,
              render: (value: string) => <StatusTag value={value} />,
            },
            {
              title: "更新时间",
              dataIndex: "updated_at",
              width: 190,
              render: formatDate,
            },
            {
              title: "操作",
              width: 100,
              render: (_value: unknown, record: Requirement) => (
                <Button
                  type="link"
                  icon={<EyeOutlined />}
                  onClick={() => navigate(`/requirements/${record.id}`)}
                >
                  查看
                </Button>
              ),
            },
          ]}
        />
      </Card>
    </>
  );
}
