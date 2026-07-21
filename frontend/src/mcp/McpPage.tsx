import {
  ApiOutlined,
  DeleteOutlined,
  EditOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useState } from "react";

import { apiClient, WorkHubApiError } from "../api/client";

const { Text, Title } = Typography;

type ToolEffect = "allow" | "confirm" | "deny";

interface McpClient {
  client_id: string;
  client_key: string;
  name: string;
  url: string;
  headers_configured: boolean;
  enabled: boolean;
  revision: number;
}

interface McpTool {
  tool_id: string;
  client_key: string;
  original_name: string;
  model_name: string;
  description: string | null;
  allowlisted: boolean;
  effect: ToolEffect;
  revision: number;
}

interface ClientForm {
  client_key: string;
  name: string;
  url: string;
  headers_json?: string;
  enabled: boolean;
}

const effectLabels: Record<ToolEffect, string> = {
  allow: "直接执行",
  confirm: "员工确认",
  deny: "拒绝",
};

export function McpPage() {
  const [clients, setClients] = useState<McpClient[]>([]);
  const [tools, setTools] = useState<McpTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<McpClient | null | undefined>(undefined);
  const [form] = Form.useForm<ClientForm>();
  const [toast, toastContext] = message.useMessage();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [nextClients, nextTools] = await Promise.all([
        apiClient.request<McpClient[]>("mcp/clients"),
        apiClient.request<McpTool[]>("mcp/tools"),
      ]);
      setClients(nextClients);
      setTools(nextTools);
      setError(null);
    } catch (reason) {
      setError(apiErrorMessage(reason));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => void load(), [load]);

  async function saveClient(values: ClientForm) {
    try {
      const headers = parseHeaders(values.headers_json);
      const path = editing ? `mcp/clients/${editing.client_id}` : "mcp/clients";
      await apiClient.request(path, {
        method: editing ? "PUT" : "POST",
        body: JSON.stringify({
          client_key: values.client_key,
          name: values.name,
          url: values.url,
          headers,
          enabled: values.enabled,
          expected_revision: editing?.revision ?? null,
        }),
      });
      setEditing(undefined);
      await load();
      toast.success(editing ? "客户端已更新" : "客户端已创建");
    } catch (reason) {
      setError(apiErrorMessage(reason));
    }
  }

  async function connect(client: McpClient) {
    try {
      const result = await apiClient.request<{ tool_count: number }>(
        `mcp/clients/${client.client_id}/connect`,
        { method: "POST" },
      );
      await load();
      toast.success(`已发现 ${result.tool_count} 个工具`);
    } catch (reason) {
      setError(apiErrorMessage(reason));
    }
  }

  async function deleteClient(client: McpClient) {
    try {
      await apiClient.request(`mcp/clients/${client.client_id}`, {
        method: "DELETE",
        body: JSON.stringify({ expected_revision: client.revision }),
      });
      await load();
      toast.success("客户端已删除");
    } catch (reason) {
      setError(apiErrorMessage(reason));
    }
  }

  async function updateTool(
    tool: McpTool,
    patch: Partial<Pick<McpTool, "allowlisted" | "effect">>,
  ) {
    try {
      const updated = await apiClient.request<McpTool>(`mcp/tools/${tool.tool_id}/setting`, {
        method: "PUT",
        body: JSON.stringify({
          allowlisted: patch.allowlisted ?? tool.allowlisted,
          effect: patch.effect ?? tool.effect,
          expected_revision: tool.revision,
        }),
      });
      setTools((current) =>
        current.map((item) => (item.tool_id === updated.tool_id ? updated : item)),
      );
    } catch (reason) {
      setError(apiErrorMessage(reason));
      await load();
    }
  }

  return (
    <>
      {toastContext}
      <section className="content-heading mcp-heading">
        <div>
          <Title level={2}>MCP</Title>
          <Text type="secondary">管理内部系统连接与 Agent 工具策略</Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} aria-label="刷新 MCP" onClick={() => void load()} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)}>
            新建客户端
          </Button>
        </Space>
      </section>

      {error ? (
        <Alert
          className="knowledge-alert"
          type="error"
          showIcon
          closable
          message={error}
          onClose={() => setError(null)}
        />
      ) : null}

      <section className="mcp-section" aria-labelledby="mcp-clients-heading">
        <Title id="mcp-clients-heading" level={3}>
          客户端
        </Title>
        <Table
          rowKey="client_id"
          size="small"
          loading={loading}
          pagination={false}
          dataSource={clients}
          locale={{ emptyText: "尚未配置 MCP 客户端" }}
          columns={[
            {
              title: "名称",
              dataIndex: "name",
              render: (value, item) => (
                <Space>
                  <ApiOutlined />
                  <Text strong>{value}</Text>
                  <Text type="secondary">{item.client_key}</Text>
                </Space>
              ),
            },
            { title: "地址", dataIndex: "url", ellipsis: true },
            {
              title: "凭据",
              dataIndex: "headers_configured",
              width: 90,
              render: (value) => (
                <Tag color={value ? "success" : "default"}>{value ? "已配置" : "无"}</Tag>
              ),
            },
            {
              title: "状态",
              dataIndex: "enabled",
              width: 90,
              render: (value) => (
                <Tag color={value ? "processing" : "default"}>{value ? "启用" : "停用"}</Tag>
              ),
            },
            {
              title: "操作",
              width: 150,
              render: (_, item) => (
                <Space size={2}>
                  <Button
                    type="text"
                    icon={<LinkOutlined />}
                    aria-label={`连接 ${item.name}`}
                    disabled={!item.enabled}
                    onClick={() => void connect(item)}
                  />
                  <Button
                    type="text"
                    icon={<EditOutlined />}
                    aria-label={`编辑 ${item.name}`}
                    onClick={() => openEditor(item)}
                  />
                  <Popconfirm title="删除客户端？" onConfirm={() => void deleteClient(item)}>
                    <Button
                      danger
                      type="text"
                      icon={<DeleteOutlined />}
                      aria-label={`删除 ${item.name}`}
                    />
                  </Popconfirm>
                </Space>
              ),
            },
          ]}
        />
      </section>

      <section className="mcp-section" aria-labelledby="mcp-tools-heading">
        <Title id="mcp-tools-heading" level={3}>
          已发现工具
        </Title>
        <Table
          rowKey="tool_id"
          size="small"
          loading={loading}
          pagination={false}
          dataSource={tools}
          locale={{ emptyText: "连接客户端后发现工具" }}
          columns={[
            {
              title: "工具",
              dataIndex: "original_name",
              render: (value, item) => (
                <div className="mcp-tool-name">
                  <Text strong>{value}</Text>
                  <Text type="secondary">{item.model_name}</Text>
                </div>
              ),
            },
            { title: "客户端", dataIndex: "client_key", width: 120 },
            {
              title: "模型白名单",
              dataIndex: "allowlisted",
              width: 120,
              render: (value, item) => (
                <Switch
                  checked={value}
                  aria-label={`${item.original_name} 模型白名单`}
                  onChange={(checked) => void updateTool(item, { allowlisted: checked })}
                />
              ),
            },
            {
              title: "执行策略",
              dataIndex: "effect",
              width: 150,
              render: (value, item) => (
                <Select
                  value={value}
                  aria-label={`${item.original_name} 执行策略`}
                  className="mcp-policy-select"
                  options={(Object.keys(effectLabels) as ToolEffect[]).map((key) => ({
                    value: key,
                    label: effectLabels[key],
                  }))}
                  onChange={(effect) => void updateTool(item, { effect })}
                />
              ),
            },
          ]}
        />
      </section>

      <Modal
        title={editing ? "编辑 MCP 客户端" : "新建 MCP 客户端"}
        open={editing !== undefined}
        okText="保存"
        cancelText="取消"
        onOk={() => form.submit()}
        onCancel={() => setEditing(undefined)}
        destroyOnHidden
      >
        <Form
          form={form}
          layout="vertical"
          preserve={false}
          onFinish={(values) => void saveClient(values)}
          initialValues={{ enabled: false }}
        >
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item
            name="client_key"
            label="稳定标识"
            rules={[{ required: true, pattern: /^[a-z][a-z0-9_]{0,47}$/ }]}
          >
            <Input placeholder="mock_oa" />
          </Form.Item>
          <Form.Item
            name="url"
            label="Streamable HTTP 地址"
            rules={[{ required: true, type: "url" }]}
          >
            <Input placeholder="http://127.0.0.1:9001/mcp" />
          </Form.Item>
          <Form.Item
            name="headers_json"
            label="Headers（JSON）"
            extra={editing?.headers_configured ? "留空将保留现有配置" : undefined}
          >
            <Input.TextArea
              rows={4}
              spellCheck={false}
              placeholder={'{"Authorization":"Bearer ..."}'}
            />
          </Form.Item>
          <Form.Item name="enabled" label="启用" valuePropName="checked">
            <Switch />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );

  function openEditor(client: McpClient | null) {
    setEditing(client);
    form.setFieldsValue(
      client
        ? {
            client_key: client.client_key,
            name: client.name,
            url: client.url,
            headers_json: "",
            enabled: client.enabled,
          }
        : { client_key: "", name: "", url: "", headers_json: "", enabled: false },
    );
  }
}

function parseHeaders(value?: string): Record<string, string> | undefined {
  if (!value?.trim()) return undefined;
  const parsed: unknown = JSON.parse(value);
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    Array.isArray(parsed) ||
    Object.values(parsed).some((item) => typeof item !== "string")
  )
    throw new Error("Headers 必须是字符串键值的 JSON 对象");
  return parsed as Record<string, string>;
}

function apiErrorMessage(reason: unknown): string {
  if (reason instanceof WorkHubApiError) return reason.message;
  if (reason instanceof Error) return reason.message;
  return "MCP 操作失败，请稍后重试。";
}
