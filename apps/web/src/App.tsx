import {
  ApartmentOutlined,
  AuditOutlined,
  CommentOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  LinkOutlined,
  LogoutOutlined,
  SearchOutlined,
  UnorderedListOutlined,
} from "@ant-design/icons";
import { Avatar, Button, ConfigProvider, Layout, Menu, Space, Typography } from "antd";
import { lazy, Suspense, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";

import { LoadingBlock } from "./components";
import { SessionContext, loadSession, saveSession } from "./session";
import type { UserSession } from "./types";

const LoginPage = lazy(() =>
  import("./pages/LoginPage").then((module) => ({ default: module.LoginPage })),
);
const ConversationPage = lazy(() =>
  import("./pages/ConversationPage").then((module) => ({
    default: module.ConversationPage,
  })),
);
const AnalysisPage = lazy(() =>
  import("./pages/AnalysisPage").then((module) => ({ default: module.AnalysisPage })),
);
const ConnectorsPage = lazy(() =>
  import("./pages/ConnectorsPage").then((module) => ({ default: module.ConnectorsPage })),
);
const RequirementDetailPage = lazy(() =>
  import("./pages/RequirementDetailPage").then((module) => ({
    default: module.RequirementDetailPage,
  })),
);
const RequirementsPage = lazy(() =>
  import("./pages/RequirementsPage").then((module) => ({
    default: module.RequirementsPage,
  })),
);
const ReviewDetailPage = lazy(() =>
  import("./pages/ReviewDetailPage").then((module) => ({
    default: module.ReviewDetailPage,
  })),
);
const ReviewsPage = lazy(() =>
  import("./pages/ReviewsPage").then((module) => ({ default: module.ReviewsPage })),
);
const SearchPage = lazy(() =>
  import("./pages/SearchPage").then((module) => ({ default: module.SearchPage })),
);
const SourceDetailPage = lazy(() =>
  import("./pages/SourceDetailPage").then((module) => ({
    default: module.SourceDetailPage,
  })),
);
const SourcesPage = lazy(() =>
  import("./pages/SourcesPage").then((module) => ({ default: module.SourcesPage })),
);

const navigation = [
  { key: "/chat", icon: <CommentOutlined />, label: "提出需求" },
  { key: "/requirements", icon: <UnorderedListOutlined />, label: "需求管理" },
  { key: "/reviews", icon: <AuditOutlined />, label: "审核中心" },
  { key: "/sources", icon: <DatabaseOutlined />, label: "原始输入" },
  { key: "/search", icon: <SearchOutlined />, label: "综合检索" },
  { key: "/analysis", icon: <FileSearchOutlined />, label: "AI 调用与失败任务" },
  { key: "/connectors", icon: <LinkOutlined />, label: "渠道配置" },
];

function ManagementApp() {
  const [session, setSession] = useState<UserSession | null>(loadSession);
  const location = useLocation();
  const navigate = useNavigate();

  if (!session) {
    return (
      <Suspense fallback={<LoadingBlock />}>
        <LoginPage
          onLogin={(value) => {
            saveSession(value);
            setSession(value);
          }}
        />
      </Suspense>
    );
  }

  return (
    <SessionContext.Provider value={session}>
      <Layout className="app-shell">
        <Layout.Sider className="app-sider" breakpoint="lg" collapsedWidth="0" width={236}>
          <div className="brand">
            <ApartmentOutlined />
            <span>需求管理 Agent</span>
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[
              navigation.find((item) => location.pathname.startsWith(item.key))?.key ??
                "/chat",
            ]}
            items={navigation}
            onClick={({ key }) => navigate(key)}
          />
        </Layout.Sider>
        <Layout>
          <Layout.Header className="app-header">
            <Typography.Text type="secondary">AI 建议 · 人工审核 · 版本可追溯</Typography.Text>
            <Space>
              <Avatar>{session.actorName.slice(0, 1).toUpperCase()}</Avatar>
              <span>{session.actorName}</span>
              <Button
                type="text"
                icon={<LogoutOutlined />}
                onClick={() => {
                  saveSession(null);
                  setSession(null);
                }}
              >
                退出
              </Button>
            </Space>
          </Layout.Header>
          <Layout.Content className="app-content">
            <Suspense fallback={<LoadingBlock />}>
              <Routes>
                <Route path="/chat" element={<ConversationPage />} />
                <Route path="/requirements" element={<RequirementsPage />} />
                <Route path="/requirements/:id" element={<RequirementDetailPage />} />
                <Route path="/reviews" element={<ReviewsPage />} />
                <Route path="/reviews/:id" element={<ReviewDetailPage />} />
                <Route path="/sources" element={<SourcesPage />} />
                <Route path="/sources/:id" element={<SourceDetailPage />} />
                <Route path="/search" element={<SearchPage />} />
                <Route path="/analysis" element={<AnalysisPage />} />
                <Route path="/connectors" element={<ConnectorsPage />} />
                <Route path="*" element={<Navigate to="/chat" replace />} />
              </Routes>
            </Suspense>
          </Layout.Content>
        </Layout>
      </Layout>
    </SessionContext.Provider>
  );
}

export function App() {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#176b5b",
          borderRadius: 8,
          colorBgLayout: "#f3f6f5",
        },
      }}
    >
      <BrowserRouter>
        <ManagementApp />
      </BrowserRouter>
    </ConfigProvider>
  );
}
