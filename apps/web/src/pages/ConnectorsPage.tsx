import { CheckCircleOutlined, ClockCircleOutlined, SafetyCertificateOutlined } from "@ant-design/icons";
import { Alert, Card, Col, Row, Space, Tag, Typography } from "antd";

import { PageTitle } from "../components";

const connectors = [
  { name: "网页表单", type: "web_form", description: "接收文本需求，使用 Idempotency-Key 防止重复事件。", ready: true },
  { name: "产品文档", type: "document", description: "支持 PDF 和 DOCX，原文件归档到 MinIO 后异步解析。", ready: true },
  { name: "截图", type: "image", description: "支持 PNG 和 JPEG，通过 PaddleOCR 提取文本。", ready: true },
  { name: "飞书", type: "feishu", description: "事件签名、消息和附件下载将在阶段 6 接入。", ready: false },
];

export function ConnectorsPage() {
  return (
    <>
      <PageTitle title="渠道配置" subtitle="所有渠道通过统一 SourceConnector 契约进入相同处理流程" />
      <Alert
        type="info"
        showIcon
        icon={<SafetyCertificateOutlined />}
        message="安全说明"
        description="密钥不在浏览器中编辑或展示。飞书凭据将在阶段 6 通过后端环境变量安全配置。"
      />
      <Row gutter={[16, 16]} className="section-card">
        {connectors.map((connector) => (
          <Col xs={24} md={12} xl={8} key={connector.type}>
            <Card
              title={connector.name}
              extra={
                connector.ready
                  ? <Tag icon={<CheckCircleOutlined />} color="success">已启用</Tag>
                  : <Tag icon={<ClockCircleOutlined />} color="default">待接入</Tag>
              }
            >
              <Space direction="vertical">
                <Typography.Text code>{connector.type}</Typography.Text>
                <Typography.Paragraph type="secondary">{connector.description}</Typography.Paragraph>
              </Space>
            </Card>
          </Col>
        ))}
      </Row>
    </>
  );
}
