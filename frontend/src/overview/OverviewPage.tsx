import { Alert, Badge, Empty, Spin, Typography } from "antd";
import { useEffect, useState } from "react";

import { apiClient, WorkHubApiError } from "../api/client";

const { Text, Title } = Typography;

interface OverviewStats {
  employees_total: number;
  employees_active: number;
  identities_unbound: number;
  knowledge_documents: number;
  knowledge_index_failed: number;
  mcp_clients: number;
  mcp_tools_exposed: number;
  pending_actions: number;
  feishu_state: string;
  mock_oa_state: string;
}

export function OverviewPage() {
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void apiClient
      .request<OverviewStats>("overview")
      .then(setStats)
      .catch((reason: unknown) => {
        setError(reason instanceof WorkHubApiError ? reason.message : "概览加载失败。");
      });
  }, []);

  return (
    <>
      <section className="content-heading">
        <Title level={2}>概览</Title>
        <Text type="secondary">实例运行与接入状态</Text>
      </section>
      {error ? (
        <Alert type="error" showIcon message={error} />
      ) : !stats ? (
        <Spin />
      ) : (
        <>
          <section className="metric-strip" aria-label="基础统计">
            <Metric label="员工" value={`${stats.employees_active}/${stats.employees_total}`} />
            <Metric
              label="待绑定身份"
              value={stats.identities_unbound}
              attention={stats.identities_unbound > 0}
            />
            <Metric label="知识文档" value={stats.knowledge_documents} />
            <Metric
              label="索引失败"
              value={stats.knowledge_index_failed}
              attention={stats.knowledge_index_failed > 0}
            />
            <Metric label="MCP 客户端" value={stats.mcp_clients} />
            <Metric label="已暴露工具" value={stats.mcp_tools_exposed} />
            <Metric
              label="处理中动作"
              value={stats.pending_actions}
              attention={stats.pending_actions > 0}
            />
          </section>
          <section aria-labelledby="service-status-heading">
            <Title id="service-status-heading" level={3}>
              系统状态
            </Title>
            <div className="status-list">
              <StatusRow name="WorkHub API" state="ready" />
              <StatusRow name="Mock OA" state={stats.mock_oa_state} />
              <StatusRow name="飞书连接" state={stats.feishu_state} />
            </div>
          </section>
        </>
      )}
      {!error && stats?.employees_total === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未创建员工" />
      ) : null}
    </>
  );
}

function Metric({
  label,
  value,
  attention = false,
}: {
  label: string;
  value: string | number;
  attention?: boolean;
}) {
  return (
    <div className="metric-item">
      <Text type="secondary">{label}</Text>
      <Text className={attention ? "metric-attention" : ""}>{value}</Text>
    </div>
  );
}

function StatusRow({ name, state }: { name: string; state: string }) {
  const ok = ["ready", "connected", "running"].includes(state);
  return (
    <div className="status-row">
      <span>
        <Badge status={ok ? "success" : state === "disabled" ? "default" : "error"} />{" "}
        <Text strong>{name}</Text>
      </span>
      <Text type="secondary">{stateLabel(state)}</Text>
    </div>
  );
}

function stateLabel(state: string) {
  return (
    (
      {
        ready: "正常",
        connected: "已连接",
        running: "运行中",
        disabled: "未配置",
        error: "异常",
        connecting: "连接中",
      } as Record<string, string>
    )[state] ?? state
  );
}
