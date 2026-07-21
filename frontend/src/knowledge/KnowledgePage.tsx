import {
  DeleteOutlined,
  EditOutlined,
  FileAddOutlined,
  FileTextOutlined,
  FolderAddOutlined,
  FolderOpenOutlined,
  ReloadOutlined,
  SaveOutlined,
} from "@ant-design/icons";
import {
  Alert,
  Button,
  Empty,
  Input,
  Modal,
  Popconfirm,
  Segmented,
  Space,
  Spin,
  Tag,
  Tree,
  Typography,
  message,
} from "antd";
import { useCallback, useEffect, useMemo, useState } from "react";

import { apiClient, WorkHubApiError } from "../api/client";

const { Text, Title } = Typography;
const { TextArea } = Input;

type NodeType = "directory" | "document";
type IndexStatus = "queued" | "indexing" | "active" | "failed" | null;

interface KnowledgeNode {
  node_id: string;
  parent_id: string | null;
  node_type: NodeType;
  name: string;
  relative_path: string;
  revision: number;
  reconciliation_required: boolean;
  version: number | null;
  index_status: IndexStatus;
  index_error_code: string | null;
}

interface KnowledgeDocument extends KnowledgeNode {
  content: string;
}

interface TreeItem {
  key: string;
  title: React.ReactNode;
  icon: React.ReactNode;
  isLeaf: boolean;
  children?: TreeItem[];
}

const statusPresentation: Record<Exclude<IndexStatus, null>, { color: string; label: string }> = {
  queued: { color: "default", label: "等待索引" },
  indexing: { color: "processing", label: "索引中" },
  active: { color: "success", label: "可检索" },
  failed: { color: "error", label: "索引失败" },
};

export function KnowledgePage() {
  const [nodes, setNodes] = useState<KnowledgeNode[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [document, setDocument] = useState<KnowledgeDocument | null>(null);
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [createType, setCreateType] = useState<NodeType>("document");
  const [createName, setCreateName] = useState("");
  const [renameOpen, setRenameOpen] = useState(false);
  const [renameName, setRenameName] = useState("");
  const [toast, toastContext] = message.useMessage();

  const selectedNode = nodes.find((node) => node.node_id === selectedId) ?? null;

  const loadNodes = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const result = await apiClient.request<KnowledgeNode[]>("knowledge/nodes");
      setNodes(result);
      setSelectedId((current) =>
        current && result.some((node) => node.node_id === current)
          ? current
          : (result.find((node) => node.node_type === "document")?.node_id ?? null),
      );
      setError(null);
    } catch (reason) {
      setError(apiErrorMessage(reason));
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadNodes();
    const timer = window.setInterval(() => void loadNodes(true), 2500);
    return () => window.clearInterval(timer);
  }, [loadNodes]);

  useEffect(() => {
    if (!selectedId || selectedNode?.node_type !== "document") {
      setDocument(null);
      setContent("");
      return;
    }
    void apiClient
      .request<KnowledgeDocument>(`knowledge/documents/${selectedId}`)
      .then((value) => {
        setDocument(value);
        setContent(value.content);
        setError(null);
      })
      .catch((reason: unknown) => setError(apiErrorMessage(reason)));
  }, [selectedId, selectedNode?.node_type]);

  const treeData = useMemo(() => buildTree(nodes), [nodes]);
  const dirty = document !== null && content !== document.content;

  async function createNode() {
    const name = createName.trim();
    if (!name) return;
    const parentId =
      selectedNode?.node_type === "directory"
        ? selectedNode.node_id
        : (selectedNode?.parent_id ?? null);
    try {
      const created = await apiClient.request<KnowledgeNode>("knowledge/nodes", {
        method: "POST",
        body: JSON.stringify({
          parent_id: parentId,
          name,
          node_type: createType,
          content: "",
        }),
      });
      setCreateOpen(false);
      setCreateName("");
      await loadNodes(true);
      setSelectedId(created.node_id);
      toast.success(createType === "directory" ? "目录已创建" : "文档已创建");
    } catch (reason) {
      setError(apiErrorMessage(reason));
    }
  }

  async function saveDocument() {
    if (!document) return;
    setSaving(true);
    try {
      const updated = await apiClient.request<KnowledgeNode>(
        `knowledge/documents/${document.node_id}`,
        {
          method: "PUT",
          body: JSON.stringify({ content, expected_revision: document.revision }),
        },
      );
      setDocument({ ...document, ...updated, content });
      await loadNodes(true);
      toast.success("文档已保存并进入索引队列");
      setError(null);
    } catch (reason) {
      const apiError = reason instanceof WorkHubApiError ? reason : null;
      setError(
        apiError?.code === "revision_conflict"
          ? "文档已被其他操作修改，请重新载入后再保存。"
          : apiErrorMessage(reason),
      );
    } finally {
      setSaving(false);
    }
  }

  async function renameNode() {
    if (!selectedNode || !renameName.trim()) return;
    try {
      await apiClient.request(`knowledge/nodes/${selectedNode.node_id}`, {
        method: "PATCH",
        body: JSON.stringify({
          parent_id: selectedNode.parent_id,
          name: renameName.trim(),
          expected_revision: selectedNode.revision,
        }),
      });
      setRenameOpen(false);
      await loadNodes(true);
      toast.success("名称已更新");
    } catch (reason) {
      setError(apiErrorMessage(reason));
    }
  }

  async function deleteNode() {
    if (!selectedNode) return;
    try {
      await apiClient.request(`knowledge/nodes/${selectedNode.node_id}`, {
        method: "DELETE",
        body: JSON.stringify({ expected_revision: selectedNode.revision }),
      });
      setSelectedId(null);
      await loadNodes(true);
      toast.success("已删除");
    } catch (reason) {
      setError(apiErrorMessage(reason));
    }
  }

  async function retryIndex() {
    if (!selectedNode) return;
    try {
      await apiClient.request(`knowledge/nodes/${selectedNode.node_id}/retry`, {
        method: "POST",
      });
      await loadNodes(true);
      toast.success("已重新排队");
    } catch (reason) {
      setError(apiErrorMessage(reason));
    }
  }

  return (
    <>
      {toastContext}
      <section className="content-heading knowledge-heading">
        <div>
          <Title level={2}>知识库</Title>
          <Text type="secondary">目录化管理制度与操作指南</Text>
        </div>
        <Space wrap>
          <Button icon={<FolderAddOutlined />} onClick={() => openCreate("directory")}>
            新建目录
          </Button>
          <Button type="primary" icon={<FileAddOutlined />} onClick={() => openCreate("document")}>
            新建文档
          </Button>
        </Space>
      </section>

      {error ? (
        <Alert className="knowledge-alert" type="error" showIcon closable message={error} />
      ) : null}

      <section className="knowledge-workspace" aria-label="知识库工作区">
        <aside className="knowledge-tree-panel">
          <div className="panel-toolbar">
            <Text strong>目录</Text>
            <Button
              type="text"
              icon={<ReloadOutlined />}
              aria-label="刷新知识目录"
              onClick={() => void loadNodes()}
            />
          </div>
          <div className="tree-scroll">
            {loading ? (
              <Spin size="small" />
            ) : treeData.length ? (
              <Tree
                showIcon
                blockNode
                defaultExpandAll
                selectedKeys={selectedId ? [selectedId] : []}
                treeData={treeData}
                onSelect={(keys) => setSelectedId(String(keys[0] ?? "") || null)}
              />
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无知识内容" />
            )}
          </div>
        </aside>

        <div className="knowledge-editor-panel">
          {selectedNode ? (
            <>
              <div className="panel-toolbar editor-toolbar">
                <div className="document-identity">
                  <Text strong>{selectedNode.name}</Text>
                  <Text type="secondary" ellipsis={{ tooltip: selectedNode.relative_path }}>
                    {selectedNode.relative_path}
                  </Text>
                </div>
                <Space wrap>
                  {selectedNode.index_status ? (
                    <IndexTag status={selectedNode.index_status} />
                  ) : null}
                  <Button
                    type="text"
                    icon={<EditOutlined />}
                    aria-label="重命名"
                    onClick={() => {
                      setRenameName(selectedNode.name);
                      setRenameOpen(true);
                    }}
                  />
                  <Popconfirm
                    title="删除所选内容？"
                    description={
                      selectedNode.node_type === "directory" ? "目录内的文档也会删除。" : undefined
                    }
                    okButtonProps={{ danger: true }}
                    onConfirm={() => void deleteNode()}
                  >
                    <Button danger type="text" icon={<DeleteOutlined />} aria-label="删除" />
                  </Popconfirm>
                </Space>
              </div>
              {selectedNode.index_status === "failed" ? (
                <div className="index-error-row">
                  <Text type="danger">{selectedNode.index_error_code ?? "索引失败"}</Text>
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => void retryIndex()}>
                    重试
                  </Button>
                </div>
              ) : null}
              {selectedNode.node_type === "document" ? (
                <>
                  <TextArea
                    className="knowledge-editor"
                    value={content}
                    spellCheck={false}
                    onChange={(event) => setContent(event.target.value)}
                    aria-label="知识文档内容"
                  />
                  <div className="editor-footer">
                    <Text type="secondary">
                      {document?.version ? `版本 ${document.version}` : "正在载入"}
                      {dirty ? " · 未保存" : ""}
                    </Text>
                    <Button
                      type="primary"
                      icon={<SaveOutlined />}
                      loading={saving}
                      disabled={!document || !dirty}
                      onClick={() => void saveDocument()}
                    >
                      保存
                    </Button>
                  </div>
                </>
              ) : (
                <div className="directory-selection">
                  <FolderOpenOutlined />
                  <Text type="secondary">选择文档开始编辑</Text>
                </div>
              )}
            </>
          ) : (
            <div className="directory-selection">
              <FileTextOutlined />
              <Text type="secondary">从左侧选择文档</Text>
            </div>
          )}
        </div>
      </section>

      <Modal
        title="新建知识内容"
        open={createOpen}
        okText="创建"
        cancelText="取消"
        okButtonProps={{ disabled: !createName.trim() }}
        onOk={() => void createNode()}
        onCancel={() => setCreateOpen(false)}
      >
        <Space direction="vertical" size={16} className="modal-fields">
          <Segmented
            block
            value={createType}
            options={[
              { label: "文档", value: "document" },
              { label: "目录", value: "directory" },
            ]}
            onChange={(value) => setCreateType(value as NodeType)}
          />
          <Input
            autoFocus
            value={createName}
            placeholder={createType === "document" ? "例如：员工请假制度.md" : "目录名称"}
            onChange={(event) => setCreateName(event.target.value)}
            onPressEnter={() => void createNode()}
          />
        </Space>
      </Modal>

      <Modal
        title="重命名"
        open={renameOpen}
        okText="保存"
        cancelText="取消"
        okButtonProps={{ disabled: !renameName.trim() }}
        onOk={() => void renameNode()}
        onCancel={() => setRenameOpen(false)}
      >
        <Input
          autoFocus
          value={renameName}
          onChange={(event) => setRenameName(event.target.value)}
          onPressEnter={() => void renameNode()}
        />
      </Modal>
    </>
  );

  function openCreate(type: NodeType) {
    setCreateType(type);
    setCreateName(type === "document" ? "untitled.md" : "");
    setCreateOpen(true);
  }
}

function IndexTag({ status }: { status: Exclude<IndexStatus, null> }) {
  const presentation = statusPresentation[status];
  return <Tag color={presentation.color}>{presentation.label}</Tag>;
}

function buildTree(nodes: KnowledgeNode[]): TreeItem[] {
  const children = new Map<string | null, KnowledgeNode[]>();
  for (const node of nodes) {
    const bucket = children.get(node.parent_id) ?? [];
    bucket.push(node);
    children.set(node.parent_id, bucket);
  }
  const visit = (parentId: string | null): TreeItem[] =>
    (children.get(parentId) ?? [])
      .sort((left, right) => {
        if (left.node_type !== right.node_type) return left.node_type === "directory" ? -1 : 1;
        return left.name.localeCompare(right.name, "zh-CN");
      })
      .map((node) => ({
        key: node.node_id,
        title: node.name,
        icon: node.node_type === "directory" ? <FolderOpenOutlined /> : <FileTextOutlined />,
        isLeaf: node.node_type === "document",
        children: node.node_type === "directory" ? visit(node.node_id) : undefined,
      }));
  return visit(null);
}

function apiErrorMessage(reason: unknown): string {
  if (reason instanceof WorkHubApiError) {
    if (reason.status === 401) return "管理员会话已失效，请登录后重试。";
    return reason.message;
  }
  return "知识库操作失败，请稍后重试。";
}
