import { ApartmentOutlined } from "@ant-design/icons";
import { Button, Card, Form, Input, Select, Typography } from "antd";

import type { UserSession } from "../types";

type LoginValues = {
  actorId: string;
  actorName: string;
  role: "reviewer" | "admin";
};

export function LoginPage({ onLogin }: { onLogin: (session: UserSession) => void }) {
  return (
    <div className="login-page">
      <Card className="login-card">
        <div className="login-brand">
          <ApartmentOutlined />
          <Typography.Title level={2}>需求管理 Agent</Typography.Title>
          <Typography.Text type="secondary">
            当前阶段使用开发身份登录，正式认证将在部署集成时接入。
          </Typography.Text>
        </div>
        <Form<LoginValues>
          layout="vertical"
          initialValues={{ role: "reviewer" }}
          onFinish={onLogin}
        >
          <Form.Item
            name="actorId"
            label="用户 ID"
            rules={[{ required: true, message: "请输入用户 ID" }]}
          >
            <Input placeholder="reviewer-001" />
          </Form.Item>
          <Form.Item
            name="actorName"
            label="显示名称"
            rules={[{ required: true, message: "请输入显示名称" }]}
          >
            <Input placeholder="产品审核人" />
          </Form.Item>
          <Form.Item name="role" label="角色">
            <Select
              options={[
                { value: "reviewer", label: "审核人" },
                { value: "admin", label: "管理员" },
              ]}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" block size="large">
            进入管理后台
          </Button>
        </Form>
      </Card>
    </div>
  );
}
