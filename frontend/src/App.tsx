import {
  ApiOutlined,
  AuditOutlined,
  BookOutlined,
  DashboardOutlined,
  LogoutOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { Button, Layout, Menu, Spin, Typography } from "antd";
import { useEffect, useState } from "react";

import { apiClient, WorkHubApiError } from "./api/client";
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
  { key: "overview", icon: <DashboardOutlined />, label: "概览" },
  { key: "employees", icon: <TeamOutlined />, label: "员工" },
  { key: "knowledge", icon: <BookOutlined />, label: "知识库" },
  { key: "mcp", icon: <ApiOutlined />, label: "MCP" },
  { key: "audit", icon: <AuditOutlined />, label: "审计" },
  { key: "settings", icon: <SettingOutlined />, label: "设置" },
];

export function App() {
  const [session, setSession] = useState<AdminSession | null | undefined>(undefined);
  const [selected, setSelected] = useState(initialPage);

  useEffect(() => {
    void apiClient
      .request<AdminSession>("auth/session")
      .then(setSession)
      .catch((reason: unknown) => {
        if (reason instanceof WorkHubApiError && reason.status === 401) setSession(null);
        else setSession(null);
      });
  }, []);

  if (session === undefined)
    return (
      <main className="session-loading">
        <Spin size="large" />
      </main>
    );
  if (session === null) return <LoginPage onLogin={setSession} />;

  async function logout() {
    try {
      await apiClient.request("auth/logout", { method: "POST" });
    } finally {
      setSession(null);
    }
  }

  function navigate(key: string) {
    setSelected(key);
    const path = key === "overview" ? "/" : `/${key}`;
    window.history.pushState({}, "", path);
  }

  return (
    <Layout className="app-shell">
      <Sider className="app-sider" width={216} breakpoint="lg" collapsedWidth={0} theme="light">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            W
          </span>
          <span>WorkHub</span>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selected]}
          items={navigation}
          onClick={({ key }) => navigate(key)}
        />
      </Sider>
      <Layout>
        <Header className="app-header">
          <Text strong>管理台</Text>
          <span className="admin-session">
            <Text type="secondary">{session.username}</Text>
            <Button
              type="text"
              icon={<LogoutOutlined />}
              aria-label="退出登录"
              onClick={() => void logout()}
            />
          </span>
        </Header>
        <Content className="app-content">
          <Page selected={selected} />
        </Content>
      </Layout>
    </Layout>
  );
}

function Page({ selected }: { selected: string }) {
  if (selected === "employees") return <EmployeesPage />;
  if (selected === "knowledge") return <KnowledgePage />;
  if (selected === "mcp") return <McpPage />;
  if (selected === "audit") return <AuditPage />;
  if (selected === "settings") return <SettingsPage />;
  return <OverviewPage />;
}

function initialPage() {
  const key = window.location.pathname.split("/").filter(Boolean)[0] ?? "overview";
  return navigation.some((item) => item.key === key) ? key : "overview";
}
