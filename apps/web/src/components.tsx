import { Alert, Empty, Spin, Tag, Typography } from "antd";
import type { ReactNode } from "react";

const colors: Record<string, string> = {
  active: "green",
  approved: "green",
  versioned: "green",
  pending: "gold",
  pending_review: "gold",
  analyzing: "blue",
  parsing: "blue",
  extracted: "cyan",
  retrieving: "cyan",
  returned: "orange",
  rejected: "red",
  failed: "red",
  deleted: "red",
  critical: "red",
  high: "volcano",
  medium: "orange",
  low: "green",
  duplicate: "purple",
  contradictory: "red",
  related: "blue",
};

export function StatusTag({ value }: { value: string | null | undefined }) {
  const normalized = value ?? "unknown";
  return <Tag color={colors[normalized] ?? "default"}>{normalized}</Tag>;
}

export function PageTitle({
  title,
  subtitle,
  extra,
}: {
  title: string;
  subtitle?: string;
  extra?: ReactNode;
}) {
  return (
    <div className="page-title">
      <div>
        <Typography.Title level={2}>{title}</Typography.Title>
        {subtitle && <Typography.Text type="secondary">{subtitle}</Typography.Text>}
      </div>
      {extra}
    </div>
  );
}

export function LoadingBlock() {
  return (
    <div className="state-block">
      <Spin size="large" />
    </div>
  );
}

export function ErrorBlock({ error }: { error: unknown }) {
  return (
    <Alert
      type="error"
      showIcon
      message="加载失败"
      description={error instanceof Error ? error.message : "未知错误"}
    />
  );
}

export function EmptyBlock({ description = "暂无数据" }: { description?: string }) {
  return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={description} />;
}

export function JsonView({ value }: { value: unknown }) {
  return <pre className="json-view">{JSON.stringify(value, null, 2)}</pre>;
}

export function formatDate(value: string | null | undefined) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}
