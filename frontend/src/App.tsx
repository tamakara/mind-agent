import {
  ApiOutlined,
  AuditOutlined,
  BookOutlined,
  DashboardOutlined,
  SettingOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { Badge, Layout, Menu, Space, Typography } from "antd";

const { Header, Content, Sider } = Layout;
const { Paragraph, Text, Title } = Typography;

const navigation = [
  { key: "overview", icon: <DashboardOutlined />, label: "概览" },
  { key: "employees", icon: <TeamOutlined />, label: "员工" },
  { key: "knowledge", icon: <BookOutlined />, label: "知识库" },
  { key: "mcp", icon: <ApiOutlined />, label: "MCP" },
  { key: "audit", icon: <AuditOutlined />, label: "审计" },
  { key: "settings", icon: <SettingOutlined />, label: "设置" },
];

const serviceStatuses = [
  { name: "WorkHub API", detail: "等待后端服务", status: "default" as const },
  { name: "Mock OA", detail: "等待后端服务", status: "default" as const },
  { name: "飞书连接", detail: "尚未配置", status: "warning" as const },
];

export function App() {
  return (
    <Layout className="app-shell">
      <Sider className="app-sider" width={224} breakpoint="lg" collapsedWidth={0} theme="light">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            W
          </span>
          <span>WorkHub</span>
        </div>
        <Menu mode="inline" selectedKeys={["overview"]} items={navigation} />
      </Sider>
      <Layout>
        <Header className="app-header">
          <Text strong>管理台</Text>
          <Text type="secondary">单企业实例</Text>
        </Header>
        <Content className="app-content">
          <section className="content-heading">
            <Title level={2}>概览</Title>
            <Paragraph type="secondary">服务接入后，这里将显示实例运行状态。</Paragraph>
          </section>

          <section aria-labelledby="service-status-heading">
            <Title id="service-status-heading" level={3}>
              系统状态
            </Title>
            <div className="status-list">
              {serviceStatuses.map((service) => (
                <div className="status-row" key={service.name}>
                  <Space size={10}>
                    <Badge status={service.status} />
                    <Text strong>{service.name}</Text>
                  </Space>
                  <Text type="secondary">{service.detail}</Text>
                </div>
              ))}
            </div>
          </section>
        </Content>
      </Layout>
    </Layout>
  );
}
