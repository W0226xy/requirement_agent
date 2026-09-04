import { ArrowLeftOutlined } from "@ant-design/icons";
import {
  Button,
  Card,
  Collapse,
  Descriptions,
  Divider,
  List,
  Segmented,
  Space,
  Steps,
  Tag,
  Typography,
} from "antd";
import { useEffect, useState } from "react";
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
import type { Requirement, RequirementVersion } from "../types";

export function RequirementDetailPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const [requirement, setRequirement] = useState<Requirement | null>(null);
  const [versions, setVersions] = useState<RequirementVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    Promise.all([
      api<Requirement>(`/api/v1/requirements/${id}`),
      api<{ items: RequirementVersion[] }>(`/api/v1/requirements/${id}/versions`),
    ])
      .then(([detail, history]) => {
        setRequirement(detail);
        setVersions(history.items);
        setSelectedVersion(history.items[0]?.version_number ?? null);
      })
      .catch(setError);
  }, [id]);

  if (error) return <ErrorBlock error={error} />;
  if (!requirement) return <LoadingBlock />;
  const current = versions.find((item) => item.version_number === selectedVersion);

  return (
    <>
      <PageTitle
        title={requirement.title}
        subtitle={requirement.requirement_key}
        extra={
          <Button icon={<ArrowLeftOutlined />} onClick={() => navigate("/requirements")}>
            返回列表
          </Button>
        }
      />
      <Card>
        <Descriptions column={{ xs: 1, sm: 2, lg: 3 }}>
          <Descriptions.Item label="状态"><StatusTag value={requirement.status} /></Descriptions.Item>
          <Descriptions.Item label="当前版本 ID">{requirement.current_version_id}</Descriptions.Item>
          <Descriptions.Item label="更新时间">{formatDate(requirement.updated_at)}</Descriptions.Item>
          <Descriptions.Item label="功能模块" span={3}>
            {requirement.functional_modules.map((item) => <Tag key={item}>{item}</Tag>)}
          </Descriptions.Item>
        </Descriptions>
      </Card>

      <Typography.Title level={3} className="section-title">当前功能与来源</Typography.Title>
      {requirement.features.length === 0 ? (
        <Card><EmptyBlock description="当前版本没有功能项" /></Card>
      ) : (
        <div className="feature-grid">
          {requirement.features.map((feature) => (
            <Card
              key={feature.feature_key}
              title={
                <Space>
                  <span>{feature.feature_title}</span>
                  <StatusTag value={feature.feature_status} />
                </Space>
              }
              extra={<Tag>{feature.feature_key}</Tag>}
            >
              <Typography.Paragraph>{feature.feature_description}</Typography.Paragraph>
              <Typography.Text strong>验收标准</Typography.Text>
              <List
                size="small"
                dataSource={feature.acceptance_criteria}
                renderItem={(item) => <List.Item>{item}</List.Item>}
              />
              <Divider />
              <Typography.Text strong>来源链路</Typography.Text>
              <Steps
                direction="vertical"
                size="small"
                items={feature.lineage.map((lineage) => ({
                  title: (
                    <Space>
                      <StatusTag value={lineage.operation_type} />
                      <Button
                        type="link"
                        onClick={() => navigate(`/sources/${lineage.source_record_id}`)}
                      >
                        来源 #{lineage.source_record_id}
                      </Button>
                    </Space>
                  ),
                  description: `${lineage.evidence_text} · 版本 ID ${lineage.introduced_version_id}`,
                }))}
              />
            </Card>
          ))}
        </div>
      )}

      <Typography.Title level={3} className="section-title">版本历史与差异</Typography.Title>
      <Card>
        <Segmented
          value={selectedVersion ?? undefined}
          options={versions.map((version) => ({
            label: `v${version.version_number}`,
            value: version.version_number,
          }))}
          onChange={(value) => setSelectedVersion(Number(value))}
        />
        {current && (
          <>
            <Descriptions className="version-meta" column={{ xs: 1, md: 3 }}>
              <Descriptions.Item label="变更类型">{current.change_type}</Descriptions.Item>
              <Descriptions.Item label="审核人">{current.reviewed_by}</Descriptions.Item>
              <Descriptions.Item label="提交时间">{formatDate(current.created_at)}</Descriptions.Item>
              <Descriptions.Item label="变更原因" span={3}>{current.change_reason}</Descriptions.Item>
            </Descriptions>
            <Collapse
              items={[
                {
                  key: "diff",
                  label: "版本差异",
                  children: <ChangeList operations={current.diff_snapshot.operations ?? []} />,
                },
                {
                  key: "snapshot",
                  label: "完整版本快照",
                  children: <JsonView value={current.requirement_snapshot} />,
                },
              ]}
            />
          </>
        )}
      </Card>
    </>
  );
}

function ChangeList({ operations }: { operations: RequirementVersion["diff_snapshot"]["operations"] }) {
  if (!operations?.length) return <EmptyBlock description="首个版本或没有差异" />;
  return (
    <List
      dataSource={operations}
      renderItem={(item) => (
        <List.Item>
          <List.Item.Meta
            title={<Space><StatusTag value={item.operation} />{item.feature_key}</Space>}
            description={item.reason}
          />
          <div className="diff-columns">
            <div><strong>变更前</strong><JsonView value={item.before} /></div>
            <div><strong>变更后</strong><JsonView value={item.after} /></div>
          </div>
        </List.Item>
      )}
    />
  );
}
