import { SearchOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, List, Select, Space, Tag, Typography } from "antd";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api";
import { EmptyBlock, ErrorBlock, PageTitle } from "../components";
import type { Candidate, PageResponse, Requirement } from "../types";

type SearchValues = {
  query: string;
  modules?: string;
  statuses?: string[];
};

export function SearchPage() {
  const navigate = useNavigate();
  const [results, setResults] = useState<Candidate[]>([]);
  const [searched, setSearched] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function search(values: SearchValues) {
    setLoading(true);
    setError(null);
    try {
      const modules = values.modules?.split(",").map((item) => item.trim()).filter(Boolean) ?? [];
      const response = await api<{ items: Candidate[] }>("/api/v1/search", {
        method: "POST",
        body: JSON.stringify({
          query: values.query,
          query_modules: modules,
          filter_modules: modules,
          statuses: values.statuses ?? ["active"],
        }),
      });
      setResults(response.items);
      setSearched(true);
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <PageTitle title="综合检索" subtitle="融合普通字段、全文检索和 pgvector 语义相似度" />
      <Card>
        <Form<SearchValues> layout="vertical" onFinish={(values) => void search(values)}>
          <Form.Item name="query" label="检索内容" rules={[{ required: true, message: "请输入检索内容" }]}>
            <Input.TextArea autoSize={{ minRows: 3, maxRows: 8 }} placeholder="描述要查找的需求或功能" />
          </Form.Item>
          <Space wrap align="end">
            <Form.Item name="modules" label="功能模块（逗号分隔）">
              <Input placeholder="reporting, export" style={{ width: 300 }} />
            </Form.Item>
            <Form.Item name="statuses" label="需求状态" initialValue={["active"]}>
              <Select
                mode="multiple"
                style={{ width: 220 }}
                options={[
                  { value: "active", label: "有效" },
                  { value: "archived", label: "已归档" },
                ]}
              />
            </Form.Item>
            <Form.Item>
              <Button type="primary" htmlType="submit" icon={<SearchOutlined />} loading={loading}>
                混合检索
              </Button>
            </Form.Item>
          </Space>
        </Form>
      </Card>
      {error && <ErrorBlock error={error} />}
      {searched && (
        <Card title={`检索结果 · ${results.length}`} className="section-card">
          {results.length ? (
            <List
              dataSource={results}
              renderItem={(item) => (
                <List.Item
                  actions={[
                    <Button
                      key="view"
                      type="link"
                      onClick={async () => {
                        try {
                          const page = await api<PageResponse<Requirement>>(
                            `/api/v1/requirements?requirement_key=${encodeURIComponent(item.requirement_key)}`,
                          );
                          if (page.items[0]) navigate(`/requirements/${page.items[0].id}`);
                        } catch (caught) {
                          setError(caught);
                        }
                      }}
                    >
                      查看详情
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Space>
                        {item.requirement_key} · {item.title}
                        <Tag color="green">{Math.round(item.similarity_score * 100)}%</Tag>
                      </Space>
                    }
                    description={
                      <>
                        <Typography.Paragraph ellipsis={{ rows: 3 }}>{item.matched_text}</Typography.Paragraph>
                        {item.functional_modules.map((module) => <Tag key={module}>{module}</Tag>)}
                      </>
                    }
                  />
                </List.Item>
              )}
            />
          ) : <EmptyBlock description="没有找到相关正式需求" />}
        </Card>
      )}
    </>
  );
}
