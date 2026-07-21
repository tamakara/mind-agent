import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgePage } from "./KnowledgePage";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("KnowledgePage", () => {
  it("loads the tree, opens a document, and exposes index state", async () => {
    const node = {
      node_id: "11111111-1111-4111-8111-111111111111",
      parent_id: null,
      node_type: "document",
      name: "员工请假制度.md",
      relative_path: "员工请假制度.md",
      revision: 2,
      reconciliation_required: false,
      version: 2,
      content_sha256: "hash",
      index_status: "active",
      index_error_code: null,
      created_at: "2026-07-21T00:00:00Z",
      updated_at: "2026-07-21T00:00:00Z",
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse([node]))
      .mockResolvedValueOnce(jsonResponse({ ...node, content: "# 请假制度\n正文" }));
    vi.stubGlobal("fetch", fetchMock);

    render(<KnowledgePage />);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(fetchMock.mock.calls[1][0]).toBe(
      "/api/v1/knowledge/documents/11111111-1111-4111-8111-111111111111",
    );

    const editor = await screen.findByLabelText("知识文档内容");
    await waitFor(() => expect(editor).toHaveValue("# 请假制度\n正文"));
    expect(screen.getByText("可检索")).toBeInTheDocument();
    expect(screen.getByText("版本 2")).toBeInTheDocument();
  });

  it("shows the structured authentication error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        jsonResponse(
          {
            error: { code: "authentication_required", message: "Authentication required" },
            request_id: "request-1",
          },
          401,
        ),
      ),
    );

    render(<KnowledgePage />);

    await waitFor(() =>
      expect(screen.getByText("管理员会话已失效，请登录后重试。")).toBeInTheDocument(),
    );
  });
});

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
