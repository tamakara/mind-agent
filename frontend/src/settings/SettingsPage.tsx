import {
  CheckCircleOutlined,
  CloudOutlined,
  DeleteOutlined,
  ReloadOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Badge,
  Button,
  Form,
  Input,
  InputNumber,
  Popconfirm,
  Segmented,
  Space,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useState } from "react";

import { apiClient, WorkHubApiError } from "../api/client";

const { Text, Title } = Typography;
type ProviderKind = "chat" | "embedding";

interface ModelSetting {
  provider_kind: ProviderKind;
  base_url: string;
  model: string;
  api_key_configured: boolean;
  revision: number;
}

interface ModelForm {
  base_url: string;
  model: string;
  api_key?: string;
}

interface FeishuSetting {
  app_id: string;
  app_secret_configured: boolean;
  revision: number;
}

interface FeishuForm {
  app_id: string;
  app_secret?: string;
}

interface FeishuStatus {
  state: string;
  configured: boolean;
  reconnect_attempt?: number;
  last_error?: string | null;
  last_apply_error?: string | null;
}

interface RuntimeSetting {
  provider_test_timeout_seconds: number;
  agent_timeout_seconds: number;
  agent_tool_timeout_seconds: number;
  agent_max_iterations: number;
  agent_context_token_budget: number;
  mcp_timeout_seconds: number;
  pending_action_ttl_seconds: number;
  feishu_reconnect_attempts: number;
  feishu_reconnect_delay_seconds: number;
  feishu_api_timeout_seconds: number;
  revision: number;
}

interface Page<T> {
  items: T[];
  total: number;
}

interface Identity {
  binding_status: "bound" | "unbound";
}

export function SettingsPage() {
  return (
    <>
      <section className="content-heading">
        <Title level={2}>设置</Title>
        <Text type="secondary">模型、飞书连接与运行参数</Text>
      </section>
      <Tabs
        items={[
          { key: "models", label: "模型", children: <ModelsPanel /> },
          { key: "feishu", label: "飞书", children: <FeishuPanel /> },
          { key: "runtime", label: "运行参数", children: <RuntimePanel /> },
        ]}
      />
    </>
  );
}

function ModelsPanel() {
  const [kind, setKind] = useState<ProviderKind>("chat");
  const [settings, setSettings] = useState<ModelSetting[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [form] = Form.useForm<ModelForm>();
  const [toast, context] = message.useMessage();
  const current = settings.find((item) => item.provider_kind === kind);
  const load = useCallback(async () => {
    try {
      setSettings(await apiClient.request<ModelSetting[]>("providers"));
      setError(null);
    } catch (reason) {
      setError(apiMessage(reason, "模型配置加载失败。"));
    }
  }, []);
  useEffect(() => void load(), [load]);
  useEffect(() => {
    form.setFieldsValue({
      base_url: current?.base_url ?? "",
      model: current?.model ?? "",
      api_key: "",
    });
  }, [current, form, kind]);

  async function save(values: ModelForm) {
    setSaving(true);
    try {
      await apiClient.request(`providers/${kind}`, {
        method: "PUT",
        body: JSON.stringify({
          base_url: values.base_url,
          model: values.model,
          api_key: values.api_key || null,
          expected_revision: current?.revision ?? null,
        }),
      });
      await load();
      form.setFieldValue("api_key", "");
      toast.success("模型配置已保存并立即生效");
    } catch (reason) {
      setError(apiMessage(reason, "模型配置保存失败。"));
    } finally {
      setSaving(false);
    }
  }

  async function testConnection() {
    setTesting(true);
    try {
      await apiClient.request(`providers/${kind}/test`, { method: "POST" });
      toast.success("连接测试成功");
    } catch (reason) {
      setError(apiMessage(reason, "连接测试失败。"));
    } finally {
      setTesting(false);
    }
  }

  return (
    <div className="settings-panel">
      {context}
      <ErrorAlert value={error} onClose={() => setError(null)} />
      <Segmented
        value={kind}
        options={[
          { label: "Chat", value: "chat" },
          { label: "Embedding", value: "embedding" },
        ]}
        onChange={(value) => setKind(value as ProviderKind)}
      />
      <div className="credential-state">
        <Tag color={current?.api_key_configured ? "success" : "warning"}>
          {current?.api_key_configured ? "密钥已配置" : "未配置"}
        </Tag>
        {current ? <Text type="secondary">revision {current.revision}</Text> : null}
      </div>
      <Form<ModelForm> form={form} layout="vertical" onFinish={(values) => void save(values)}>
        <Form.Item name="base_url" label="Base URL" rules={[{ required: true, type: "url" }]}>
          <Input placeholder="https://api.openai.com/v1" />
        </Form.Item>
        <Form.Item name="model" label="模型" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item
          name="api_key"
          label="API Key"
          extra={current?.api_key_configured ? "留空将保留现有密钥" : undefined}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Space>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
            保存
          </Button>
          <Button
            icon={<CloudOutlined />}
            loading={testing}
            disabled={!current}
            onClick={() => void testConnection()}
          >
            测试连接
          </Button>
        </Space>
      </Form>
    </div>
  );
}

function FeishuPanel() {
  const [setting, setSetting] = useState<FeishuSetting | null>(null);
  const [status, setStatus] = useState<FeishuStatus | null>(null);
  const [unbound, setUnbound] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [form] = Form.useForm<FeishuForm>();
  const [toast, context] = message.useMessage();
  const load = useCallback(async () => {
    try {
      const [nextSetting, nextStatus, identities] = await Promise.all([
        apiClient.request<FeishuSetting | null>("settings/feishu"),
        apiClient.request<FeishuStatus>("feishu/status"),
        apiClient.request<Page<Identity>>("channel-identities?binding_status=unbound&limit=1"),
      ]);
      setSetting(nextSetting);
      setStatus(nextStatus);
      setUnbound(identities.total);
      form.setFieldsValue({ app_id: nextSetting?.app_id ?? "", app_secret: "" });
      setError(null);
    } catch (reason) {
      setError(apiMessage(reason, "飞书配置加载失败。"));
    }
  }, [form]);
  useEffect(() => void load(), [load]);

  async function save(values: FeishuForm) {
    setSaving(true);
    try {
      await apiClient.request("settings/feishu", {
        method: "PUT",
        body: JSON.stringify({
          app_id: values.app_id,
          app_secret: values.app_secret || null,
          expected_revision: setting?.revision ?? null,
        }),
      });
      await load();
      toast.success("飞书配置已保存，长连接正在重建");
    } catch (reason) {
      setError(apiMessage(reason, "飞书配置保存失败。"));
    } finally {
      setSaving(false);
    }
  }

  async function remove() {
    if (!setting) return;
    try {
      await apiClient.request("settings/feishu", {
        method: "DELETE",
        body: JSON.stringify({ expected_revision: setting.revision }),
      });
      await load();
      toast.success("飞书连接已停用");
    } catch (reason) {
      setError(apiMessage(reason, "飞书配置删除失败。"));
    }
  }

  async function testConnection() {
    setTesting(true);
    try {
      await apiClient.request("settings/feishu/test", { method: "POST" });
      await load();
      toast.success("飞书连接测试已启动");
    } catch (reason) {
      setError(apiMessage(reason, "飞书连接测试失败。"));
    } finally {
      setTesting(false);
    }
  }

  const connected = status?.state === "connected";
  return (
    <div className="settings-panel">
      {context}
      <ErrorAlert value={error} onClose={() => setError(null)} />
      <div className="status-list">
        <div className="status-row">
          <Text strong>
            <Badge status={connected ? "success" : "default"} /> 长连接
          </Text>
          <Text>{status?.state ?? "载入中"}</Text>
        </div>
        <div className="status-row">
          <Text strong>重连次数</Text>
          <Text>{status?.reconnect_attempt ?? 0}</Text>
        </div>
        <div className="status-row">
          <Text strong>待绑定身份</Text>
          <Tag color={unbound ? "warning" : "success"}>{unbound}</Tag>
        </div>
      </div>
      {status?.last_error || status?.last_apply_error ? (
        <Alert type="warning" showIcon message={status.last_apply_error ?? status.last_error} />
      ) : null}
      <Form<FeishuForm> form={form} layout="vertical" onFinish={(values) => void save(values)}>
        <Form.Item name="app_id" label="App ID" rules={[{ required: true }]}>
          <Input />
        </Form.Item>
        <Form.Item
          name="app_secret"
          label="App Secret"
          extra={setting?.app_secret_configured ? "留空将保留现有密钥" : undefined}
        >
          <Input.Password autoComplete="new-password" />
        </Form.Item>
        <Space wrap>
          <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
            保存并连接
          </Button>
          <Button
            icon={connected ? <CheckCircleOutlined /> : <ReloadOutlined />}
            loading={testing}
            disabled={!setting}
            onClick={() => void testConnection()}
          >
            测试连接
          </Button>
          <Popconfirm title="确定停用飞书连接？" onConfirm={() => void remove()}>
            <Button danger icon={<DeleteOutlined />} disabled={!setting}>
              停用
            </Button>
          </Popconfirm>
        </Space>
      </Form>
    </div>
  );
}

function RuntimePanel() {
  const [setting, setSetting] = useState<RuntimeSetting | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [form] = Form.useForm<RuntimeSetting>();
  const [toast, context] = message.useMessage();
  const load = useCallback(async () => {
    try {
      const value = await apiClient.request<RuntimeSetting>("settings/runtime");
      setSetting(value);
      form.setFieldsValue(value);
      setError(null);
    } catch (reason) {
      setError(apiMessage(reason, "运行参数加载失败。"));
    }
  }, [form]);
  useEffect(() => void load(), [load]);

  async function save(values: RuntimeSetting) {
    setSaving(true);
    try {
      const saved = await apiClient.request<RuntimeSetting>("settings/runtime", {
        method: "PUT",
        body: JSON.stringify({ ...values, expected_revision: setting?.revision ?? null }),
      });
      setSetting(saved);
      form.setFieldsValue(saved);
      toast.success("运行参数已保存并立即生效");
    } catch (reason) {
      setError(apiMessage(reason, "运行参数保存失败。"));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-panel runtime-settings">
      {context}
      <ErrorAlert value={error} onClose={() => setError(null)} />
      <Form<RuntimeSetting> form={form} layout="vertical" onFinish={(values) => void save(values)}>
        <NumberField
          name="provider_test_timeout_seconds"
          label="Provider 测试超时（秒）"
          min={0.1}
          max={60}
        />
        <NumberField name="agent_timeout_seconds" label="Agent 总超时（秒）" min={0.1} max={300} />
        <NumberField name="agent_tool_timeout_seconds" label="工具超时（秒）" min={0.1} max={120} />
        <NumberField name="agent_max_iterations" label="Agent 最大迭代次数" min={1} max={32} />
        <NumberField
          name="agent_context_token_budget"
          label="上下文 Token 预算"
          min={512}
          max={1_000_000}
        />
        <NumberField name="mcp_timeout_seconds" label="MCP 超时（秒）" min={0.1} max={120} />
        <NumberField
          name="pending_action_ttl_seconds"
          label="确认动作有效期（秒）"
          min={60}
          max={3600}
        />
        <NumberField name="feishu_reconnect_attempts" label="飞书重连次数" min={0} max={20} />
        <NumberField
          name="feishu_reconnect_delay_seconds"
          label="飞书重连间隔（秒）"
          min={0}
          max={60}
        />
        <NumberField
          name="feishu_api_timeout_seconds"
          label="飞书 API 超时（秒）"
          min={0.1}
          max={60}
        />
        <Button type="primary" htmlType="submit" icon={<SaveOutlined />} loading={saving}>
          保存
        </Button>
      </Form>
    </div>
  );
}

function NumberField({
  name,
  label,
  min,
  max,
}: {
  name: keyof RuntimeSetting;
  label: string;
  min: number;
  max: number;
}) {
  return (
    <Form.Item name={name} label={label} rules={[{ required: true }]}>
      <InputNumber min={min} max={max} style={{ width: "100%" }} />
    </Form.Item>
  );
}

function ErrorAlert({ value, onClose }: { value: string | null; onClose: () => void }) {
  return value ? <Alert type="error" showIcon closable message={value} onClose={onClose} /> : null;
}

function apiMessage(reason: unknown, fallback: string) {
  return reason instanceof WorkHubApiError ? reason.message : fallback;
}
