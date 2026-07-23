import {
  ApiOutlined,
  AuditOutlined,
  BookOutlined,
  DashboardOutlined,
  LogoutOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { QueryClient, QueryClientProvider, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Layout, Menu, Spin, Typography } from "antd";
import { useState } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";

import { apiClient } from "./api/client";
import { AuditPage } from "./audit/AuditPage";
import { type AdminSession, LoginPage } from "./auth/LoginPage";
import { EmployeesPage } from "./employees/EmployeesPage";
import { KnowledgePage } from "./knowledge/KnowledgePage";
import { McpPage } from "./mcp/McpPage";
import { OverviewPage } from "./overview/OverviewPage";
import { SettingsPage } from "./settings/SettingsPage";

const { Header, Content, Sider } = Layout;
const { Text } = Typography;

const navigation = [
  { key: "/", icon: <DashboardOutlined />, label: "概览" },
  { key: "/employees", icon: <TeamOutlined />, label: "员工" },
  { key: "/knowledge", icon: <BookOutlined />, label: "知识库" },
  { key: "/mcp", icon: <ApiOutlined />, label: "MCP" },
  { key: "/audit", icon: <AuditOutlined />, label: "审计" },
  { key: "/settings", icon: <SettingOutlined />, label: "设置" },
];

export function App() {
  const [appQueryClient] = useState(
    () => new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, refetchOnWindowFocus: false } } }),
  );
  return (
    <QueryClientProvider client={appQueryClient}>
      <BrowserRouter>
        <AppContent />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

function AppContent() {
  const queryClient = useQueryClient();
  const session = useQuery<AdminSession | null>({
    queryKey: ["auth", "session"],
    queryFn: () => apiClient.request<AdminSession>("auth/session"),
    retry: false,
    throwOnError: false,
  });

  if (session.isPending) {
    return (
      <main className="session-loading">
        <Spin size="large" />
      </main>
    );
  }
  if (session.isError || !session.data) {
    return <LoginPage onLogin={(value) => void queryClient.setQueryData(["auth", "session"], value)} />;
  }
  return <AuthenticatedApp session={session.data} />;
}

function AuthenticatedApp({ session }: { session: AdminSession }) {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();

  async function logout() {
    try {
      await apiClient.request("auth/logout", { method: "POST" });
    } finally {
      queryClient.setQueryData(["auth", "session"], null);
      navigate("/", { replace: true });
    }
  }

  return (
    <Layout className="app-shell">
      <Sider className="app-sider" width={216} breakpoint="lg" collapsedWidth={0} theme="light">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">W</span>
          <span>WorkHub</span>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[location.pathname === "/" ? "/" : `/${location.pathname.split("/")[1]}`]}
          items={navigation}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <Text strong>管理台</Text>
          <span className="admin-session">
            <Text type="secondary">{session.username}</Text>
            <Button type="text" icon={<LogoutOutlined />} aria-label="退出登录" onClick={() => void logout()} />
          </span>
        </Header>
        <Content className="app-content">
          <Routes>
            <Route path="/" element={<OverviewPage />} />
            <Route path="/employees" element={<EmployeesPage />} />
            <Route path="/knowledge" element={<KnowledgePage />} />
            <Route path="/mcp" element={<McpPage />} />
            <Route path="/audit" element={<AuditPage />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Content>
      </Layout>
    </Layout>
  );
}
