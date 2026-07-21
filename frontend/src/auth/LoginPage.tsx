import { LockOutlined, UserOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, Typography } from "antd";
import { useState } from "react";

import { apiClient, WorkHubApiError } from "../api/client";

const { Text, Title } = Typography;

interface LoginValues {
  username: string;
  password: string;
}

export interface AdminSession {
  admin_user_id: string;
  username: string;
  expires_at: string;
}

export function LoginPage({ onLogin }: { onLogin: (session: AdminSession) => void }) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(values: LoginValues) {
    setSubmitting(true);
    try {
      const session = await apiClient.request<AdminSession>("auth/login", {
        method: "POST",
        body: JSON.stringify(values),
      });
      onLogin(session);
    } catch (reason) {
      setError(
        reason instanceof WorkHubApiError ? reason.message : "登录失败，请检查服务连接后重试。",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-panel" aria-labelledby="login-heading">
        <div className="login-brand">
          <span className="brand-mark" aria-hidden="true">
            W
          </span>
          <Text strong>WorkHub</Text>
        </div>
        <Title id="login-heading" level={1}>
          管理员登录
        </Title>
        <Text type="secondary">单企业实例</Text>
        {error ? <Alert type="error" showIcon message={error} /> : null}
        <Form<LoginValues> layout="vertical" onFinish={(values) => void submit(values)}>
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input autoFocus prefix={<UserOutlined />} autoComplete="username" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password prefix={<LockOutlined />} autoComplete="current-password" />
          </Form.Item>
          <Button block type="primary" htmlType="submit" loading={submitting}>
            登录
          </Button>
        </Form>
      </section>
    </main>
  );
}
