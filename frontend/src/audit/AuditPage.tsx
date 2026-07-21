import { ReloadOutlined, SearchOutlined } from "@ant-design/icons";
import { Alert, Button, Form, Input, Space, Table, Tag, Typography } from "antd";
import { useCallback, useEffect, useState } from "react";

import { apiClient, WorkHubApiError } from "../api/client";

const { Text, Title } = Typography;
interface Page<T> {
  items: T[];
  total: number;
}
interface AuditEvent {
  event_id: string;
  actor_type: string;
  actor_id: string | null;
  event_type: string;
  subject_type: string | null;
  subject_id: string | null;
  summary: Record<string, unknown>;
  error_code: string | null;
  request_id: string | null;
  created_at: string;
}
interface Filters {
  employee_id?: string;
  event?: string;
  tool?: string;
  action_id?: string;
  business_id?: string;
  result?: string;
}

export function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<Filters>({});
  const load = useCallback(
    async (values: Filters = filters) => {
      setLoading(true);
      try {
        const query = new URLSearchParams({ limit: "100" });
        Object.entries(values).forEach(([key, value]) => {
          if (value?.trim()) query.set(key, value.trim());
        });
        const page = await apiClient.request<Page<AuditEvent>>(`audit-events?${query}`);
        setEvents(page.items);
        setTotal(page.total);
        setError(null);
      } catch (reason) {
        setError(reason instanceof WorkHubApiError ? reason.message : "审计事件加载失败。");
      } finally {
        setLoading(false);
      }
    },
    [filters],
  );
  useEffect(() => void load({}), []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <section className="content-heading page-heading">
        <div>
          <Title level={2}>审计</Title>
          <Text type="secondary">脱敏操作与执行记录 · {total}</Text>
        </div>
        <Button icon={<ReloadOutlined />} onClick={() => void load()}>
          刷新
        </Button>
      </section>
      {error ? <Alert className="knowledge-alert" type="error" showIcon message={error} /> : null}
      <Form<Filters>
        layout="inline"
        className="audit-filters"
        onFinish={(values) => {
          setFilters(values);
          void load(values);
        }}
      >
        <Form.Item name="employee_id">
          <Input placeholder="员工 ID" />
        </Form.Item>
        <Form.Item name="event">
          <Input placeholder="事件" />
        </Form.Item>
        <Form.Item name="tool">
          <Input placeholder="工具" />
        </Form.Item>
        <Form.Item name="action_id">
          <Input placeholder="动作 ID" />
        </Form.Item>
        <Form.Item name="business_id">
          <Input placeholder="业务单号" />
        </Form.Item>
        <Form.Item name="result">
          <Input placeholder="结果/错误" />
        </Form.Item>
        <Button htmlType="submit" type="primary" icon={<SearchOutlined />}>
          筛选
        </Button>
      </Form>
      <Table
        rowKey="event_id"
        size="small"
        loading={loading}
        pagination={false}
        dataSource={events}
        columns={[
          {
            title: "时间",
            dataIndex: "created_at",
            width: 190,
            render: (value) => new Date(value).toLocaleString(),
          },
          {
            title: "事件",
            dataIndex: "event_type",
            width: 210,
            render: (value, item) => (
              <Space direction="vertical" size={0}>
                <Text strong>{value}</Text>
                {item.error_code ? <Tag color="error">{item.error_code}</Tag> : null}
              </Space>
            ),
          },
          {
            title: "主体",
            render: (_, item) => (
              <Space direction="vertical" size={0}>
                <Text>
                  {item.actor_type}
                  {item.actor_id ? ` · ${item.actor_id}` : ""}
                </Text>
                <Text type="secondary">
                  {item.subject_type ?? "-"}
                  {item.subject_id ? ` · ${item.subject_id}` : ""}
                </Text>
              </Space>
            ),
          },
          {
            title: "脱敏摘要",
            dataIndex: "summary",
            render: (value) => (
              <Text code className="audit-summary">
                {JSON.stringify(value)}
              </Text>
            ),
          },
          { title: "Request ID", dataIndex: "request_id", width: 150, ellipsis: true },
        ]}
      />
    </>
  );
}
