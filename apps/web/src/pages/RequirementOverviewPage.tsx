import { EyeOutlined, ReloadOutlined } from "@ant-design/icons";
import { Alert, Button, Card, Collapse, Empty, Input, Select, Space, Spin, Statistic, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, queryString } from "../api";
import { ErrorBlock, PageTitle, StatusTag } from "../components";
import type { RequirementOverview } from "../types";

export function requirementOverviewPath(keyword: string, status: string, module: string) {
  return `/api/v1/requirements/overview${queryString({ keyword, status, module })}`;
}

export function RequirementOverviewPage() {
  const navigate = useNavigate();
  const [data, setData] = useState<RequirementOverview | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const [keyword, setKeyword] = useState("");
  const [status, setStatus] = useState("");
  const [module, setModule] = useState("");
  const [moduleOptions, setModuleOptions] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await api<RequirementOverview>(requirementOverviewPath(keyword, status, module)));
    } catch (caught) {
      setError(caught);
    } finally {
      setLoading(false);
    }
  }, [keyword, module, status]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    void api<{ items: string[] }>("/api/v1/requirements/modules")
      .then((response) => setModuleOptions(response.items))
      .catch((caught) => setError(caught));
  }, []);

  const modules = data?.modules ?? [];
  const refreshOverview = async (name: string) => {
    await api(`/api/v1/requirements/overview/modules/${encodeURIComponent(name)}/refresh`, { method: "POST" });
    void load();
  };
  return (
    <>
      <PageTitle title="需求总览" subtitle="按功能模块浏览已审核的正式需求" extra={<Button icon={<ReloadOutlined />} onClick={() => void load()}>刷新</Button>} />
      <Card style={{ marginBottom: 16 }}>
        <Space className="filters" wrap>
          <Statistic title="总需求数" value={data?.total_requirements ?? 0} />
          <Statistic title="模块数" value={modules.length} />
          <Input.Search allowClear placeholder="搜索编号、标题或描述" style={{ width: 260 }} onSearch={setKeyword} />
          <Select value={module} style={{ width: 180 }} options={[{ value: "", label: "全部模块" }, ...moduleOptions.map((value) => ({ value, label: value }))]} onChange={setModule} />
          <Select allowClear placeholder="需求状态" style={{ width: 150 }} options={[{ value: "active", label: "有效" }, { value: "archived", label: "已归档" }]} onChange={(value) => setStatus(value ?? "")} />
        </Space>
      </Card>
      {error !== null && <ErrorBlock error={error} />}
      {!loading && error === null && modules.length === 0 && <Card><Empty description="暂无符合筛选条件的正式需求" /></Card>}
      {loading ? <Card><Spin /></Card> : <Collapse
        items={modules.map((group) => ({
          key: group.name,
          label: <Space wrap><Typography.Text strong>{group.name}</Typography.Text><Tag>{group.requirement_count} 条需求</Tag>{Object.entries(group.status_counts).map(([value, count]) => <Tag key={value}>{value}: {count}</Tag>)}</Space>,
          children: <Space direction="vertical" size="middle" style={{ width: "100%" }}>
            <ModuleOverviewCard group={group} onRefresh={() => void refreshOverview(group.name)} />
            {group.requirements.map((requirement) => <Card key={requirement.id} size="small">
              <Space direction="vertical" size={4} style={{ width: "100%" }}>
                <Space wrap><Typography.Text code>{requirement.requirement_key}</Typography.Text><Typography.Text strong>{requirement.title}</Typography.Text><StatusTag value={requirement.status} /></Space>
                <Typography.Paragraph type="secondary" style={{ marginBottom: 0 }}>{requirement.description}</Typography.Paragraph>
                <Button type="link" icon={<EyeOutlined />} style={{ paddingLeft: 0, width: "fit-content" }} onClick={() => navigate(`/requirements/${requirement.id}`)}>查看详情</Button>
              </Space>
            </Card>)}
          </Space>,
        }))}
      />}
    </>
  );
}

function ModuleOverviewCard({ group, onRefresh }: { group: RequirementOverview["modules"][number]; onRefresh: () => void }) {
  const overview = group.module_overview;
  if (!overview) return <Card size="small"><Typography.Text type="secondary">模块需求概述等待生成。</Typography.Text><Button type="link" onClick={onRefresh}>重新生成</Button></Card>;
  if (overview.status === "updating") return <Card size="small" title="模块需求概述"><Spin size="small" /> <Typography.Text type="secondary">正在基于最新需求更新概述…</Typography.Text></Card>;
  if (overview.status === "empty") return <Card size="small" title="模块需求概述"><Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="该模块暂无 active 正式需求" /></Card>;
  return <Card size="small" title="模块需求概述" extra={<Button type="link" onClick={onRefresh}>重新生成</Button>}>
    <Space direction="vertical" size="small" style={{ width: "100%" }}>
      {overview.status === "failed" && <Alert type="warning" showIcon message="更新失败，当前展示上次成功版本" />}
      {overview.overview && <Typography.Paragraph style={{ marginBottom: 0 }}>{overview.overview}</Typography.Paragraph>}
      {overview.core_capabilities.length > 0 && <><Typography.Text strong>核心功能</Typography.Text><Space wrap>{overview.core_capabilities.map((item) => <Tag key={item}>{item}</Tag>)}</Space></>}
      {overview.pending_items.length > 0 && <><Typography.Text strong>待确认项</Typography.Text>{overview.pending_items.map((item) => <Typography.Text key={item}>- {item}</Typography.Text>)}</>}
      <Typography.Text type="secondary">引用需求：{overview.referenced_requirement_keys.join("、") || "无"}</Typography.Text>
      {overview.updated_at && <Typography.Text type="secondary">最后更新：{new Date(overview.updated_at).toLocaleString()}</Typography.Text>}
    </Space>
  </Card>;
}
