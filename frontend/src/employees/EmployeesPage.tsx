import {
  EditOutlined,
  LinkOutlined,
  PlusOutlined,
  ReloadOutlined,
  StopOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useState } from "react";

import { apiClient, WorkHubApiError } from "../api/client";

const { Text, Title } = Typography;

interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}
interface Employee {
  employee_id: string;
  employee_no: string;
  display_name: string;
  department: string;
  manager_employee_id: string | null;
  timezone: string;
  status: "active" | "disabled";
  revision: number;
}
interface Identity {
  identity_id: string;
  app_id: string;
  platform_user_id: string;
  display_name: string | null;
  binding_status: "unbound" | "bound";
  employee_id: string | null;
  revision: number;
}
interface EmployeeForm {
  employee_no: string;
  display_name: string;
  department: string;
  timezone: string;
  manager_employee_id?: string;
}

export function EmployeesPage() {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [identities, setIdentities] = useState<Identity[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Employee | null | undefined>(undefined);
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [form] = Form.useForm<EmployeeForm>();
  const [toast, toastContext] = message.useMessage();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [employeePage, identityPage] = await Promise.all([
        apiClient.request<Page<Employee>>("employees?limit=100"),
        apiClient.request<Page<Identity>>("channel-identities?limit=100"),
      ]);
      setEmployees(employeePage.items);
      setIdentities(identityPage.items);
      setError(null);
    } catch (reason) {
      setError(apiMessage(reason, "员工目录加载失败。"));
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => void load(), [load]);

  async function save(values: EmployeeForm) {
    try {
      if (editing) {
        await apiClient.request(`employees/${editing.employee_id}`, {
          method: "PATCH",
          body: JSON.stringify({
            ...values,
            manager_employee_id: values.manager_employee_id || null,
            expected_revision: editing.revision,
          }),
        });
      } else {
        await apiClient.request("employees", {
          method: "POST",
          body: JSON.stringify({
            ...values,
            manager_employee_id: values.manager_employee_id || null,
          }),
        });
      }
      setEditing(undefined);
      await load();
      toast.success(editing ? "员工已更新" : "员工已创建");
    } catch (reason) {
      setError(apiMessage(reason, "员工保存失败。"));
    }
  }

  async function toggle(employee: Employee, active: boolean) {
    try {
      await apiClient.request(`employees/${employee.employee_id}`, {
        method: "PATCH",
        body: JSON.stringify({
          expected_revision: employee.revision,
          status: active ? "active" : "disabled",
        }),
      });
      await load();
    } catch (reason) {
      setError(apiMessage(reason, "员工状态更新失败。"));
    }
  }

  async function bind(identity: Identity) {
    const employeeId = bindings[identity.identity_id];
    if (!employeeId) return;
    try {
      await apiClient.request(`channel-identities/${identity.identity_id}/bind`, {
        method: "POST",
        body: JSON.stringify({ employee_id: employeeId, expected_revision: identity.revision }),
      });
      await load();
      toast.success("身份已绑定");
    } catch (reason) {
      setError(apiMessage(reason, "身份绑定失败。"));
    }
  }

  async function unbind(identity: Identity) {
    try {
      await apiClient.request(`channel-identities/${identity.identity_id}/unbind`, {
        method: "POST",
        body: JSON.stringify({ expected_revision: identity.revision }),
      });
      await load();
      toast.success("身份已解绑");
    } catch (reason) {
      setError(apiMessage(reason, "身份解绑失败。"));
    }
  }

  return (
    <>
      {toastContext}
      <section className="content-heading page-heading">
        <div>
          <Title level={2}>员工</Title>
          <Text type="secondary">员工目录与飞书身份</Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} aria-label="刷新员工" onClick={() => void load()} />
          <Button type="primary" icon={<PlusOutlined />} onClick={() => openEditor(null)}>
            新建员工
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
      <Tabs
        items={[
          {
            key: "employees",
            label: `员工 ${employees.length}`,
            children: (
              <Table
                rowKey="employee_id"
                size="small"
                loading={loading}
                pagination={false}
                dataSource={employees}
                columns={[
                  {
                    title: "员工",
                    render: (_, item) => (
                      <div className="table-primary">
                        <Text strong>{item.display_name}</Text>
                        <Text type="secondary">{item.employee_no}</Text>
                      </div>
                    ),
                  },
                  { title: "部门", dataIndex: "department" },
                  { title: "时区", dataIndex: "timezone" },
                  {
                    title: "启用",
                    width: 90,
                    render: (_, item) => (
                      <Switch
                        checked={item.status === "active"}
                        aria-label={`${item.display_name} 启用状态`}
                        onChange={(checked) => void toggle(item, checked)}
                      />
                    ),
                  },
                  {
                    title: "操作",
                    width: 70,
                    render: (_, item) => (
                      <Button
                        type="text"
                        icon={<EditOutlined />}
                        aria-label={`编辑 ${item.display_name}`}
                        onClick={() => openEditor(item)}
                      />
                    ),
                  },
                ]}
              />
            ),
          },
          {
            key: "identities",
            label: `飞书身份 ${identities.length}`,
            children: (
              <Table
                rowKey="identity_id"
                size="small"
                loading={loading}
                pagination={false}
                dataSource={identities}
                columns={[
                  {
                    title: "飞书用户",
                    render: (_, item) => (
                      <div className="table-primary">
                        <Text strong>{item.display_name || item.platform_user_id}</Text>
                        <Text type="secondary">{item.platform_user_id}</Text>
                      </div>
                    ),
                  },
                  {
                    title: "状态",
                    width: 100,
                    render: (_, item) => (
                      <Tag color={item.binding_status === "bound" ? "success" : "warning"}>
                        {item.binding_status === "bound" ? "已绑定" : "待绑定"}
                      </Tag>
                    ),
                  },
                  {
                    title: "员工",
                    render: (_, item) =>
                      item.binding_status === "bound" ? (
                        <Text>
                          {employees.find((employee) => employee.employee_id === item.employee_id)
                            ?.display_name ?? item.employee_id}
                        </Text>
                      ) : (
                        <Select
                          showSearch
                          optionFilterProp="label"
                          placeholder="选择员工"
                          value={bindings[item.identity_id]}
                          className="identity-select"
                          options={employees
                            .filter((employee) => employee.status === "active")
                            .map((employee) => ({
                              value: employee.employee_id,
                              label: `${employee.display_name} · ${employee.employee_no}`,
                            }))}
                          onChange={(value) =>
                            setBindings((current) => ({ ...current, [item.identity_id]: value }))
                          }
                        />
                      ),
                  },
                  {
                    title: "操作",
                    width: 90,
                    render: (_, item) =>
                      item.binding_status === "bound" ? (
                        <Button
                          type="text"
                          danger
                          icon={<StopOutlined />}
                          onClick={() => void unbind(item)}
                        >
                          解绑
                        </Button>
                      ) : (
                        <Button
                          type="text"
                          icon={<LinkOutlined />}
                          disabled={!bindings[item.identity_id]}
                          onClick={() => void bind(item)}
                        >
                          绑定
                        </Button>
                      ),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      <Modal
        title={editing ? "编辑员工" : "新建员工"}
        open={editing !== undefined}
        okText="保存"
        cancelText="取消"
        destroyOnHidden
        onOk={() => form.submit()}
        onCancel={() => setEditing(undefined)}
      >
        <Form<EmployeeForm>
          form={form}
          layout="vertical"
          preserve={false}
          onFinish={(values) => void save(values)}
        >
          <Form.Item
            name="employee_no"
            label="员工号"
            rules={[{ required: true, pattern: /^[A-Za-z0-9._-]+$/ }]}
          >
            <Input />
          </Form.Item>
          <Form.Item name="display_name" label="姓名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="department" label="部门" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="manager_employee_id" label="主管">
            <Select
              allowClear
              options={employees
                .filter((employee) => employee.employee_id !== editing?.employee_id)
                .map((employee) => ({ value: employee.employee_id, label: employee.display_name }))}
            />
          </Form.Item>
          <Form.Item name="timezone" label="时区" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </>
  );

  function openEditor(employee: Employee | null) {
    setEditing(employee);
    form.setFieldsValue(
      employee
        ? {
            employee_no: employee.employee_no,
            display_name: employee.display_name,
            department: employee.department,
            manager_employee_id: employee.manager_employee_id ?? undefined,
            timezone: employee.timezone,
          }
        : { employee_no: "", display_name: "", department: "", timezone: "Asia/Shanghai" },
    );
  }
}

function apiMessage(reason: unknown, fallback: string) {
  return reason instanceof WorkHubApiError ? reason.message : fallback;
}
