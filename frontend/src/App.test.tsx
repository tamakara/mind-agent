import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { App } from "./App";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("App", () => {
  it("restores an authenticated session and renders the compact menu", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input) => {
        const url = String(input);
        if (url.endsWith("/auth/session")) return jsonResponse(session);
        if (url.endsWith("/overview")) return jsonResponse(overview);
        return jsonResponse(
          { error: { code: "not_found", message: "Not found" }, request_id: "1" },
          404,
        );
      }),
    );
    render(<App />);

    expect(await screen.findByRole("heading", { name: "概览", level: 2 })).toBeInTheDocument();
    for (const item of ["概览", "员工", "知识库", "MCP", "审计", "设置"]) {
      expect(screen.getByRole("menuitem", { name: new RegExp(`${item}$`) })).toBeInTheDocument();
    }
    expect(screen.queryByText("测试聊天")).not.toBeInTheDocument();
    expect(screen.queryByText("业务审批")).not.toBeInTheDocument();
  });

  it("logs in after an expired session", async () => {
    const fetchMock = vi.fn(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/session")) return errorResponse(401, "authentication_required");
      if (url.endsWith("/auth/login")) return jsonResponse(session);
      if (url.endsWith("/overview")) return jsonResponse(overview);
      return errorResponse(404, "not_found");
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);
    expect(await screen.findByRole("heading", { name: "管理员登录" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("用户名"), { target: { value: "admin" } });
    fireEvent.change(screen.getByLabelText("密码"), { target: { value: "password" } });
    fireEvent.click(screen.getByRole("button", { name: /登\s*录/ }));

    expect(await screen.findByRole("heading", { name: "概览", level: 2 })).toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/login",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });
});

const session = { admin_user_id: "admin-1", username: "admin", expires_at: "2026-07-22T10:00:00Z" };
const overview = {
  employees_total: 1,
  employees_active: 1,
  identities_unbound: 0,
  knowledge_documents: 1,
  knowledge_index_failed: 0,
  mcp_clients: 1,
  mcp_tools_exposed: 3,
  pending_actions: 0,
  feishu_state: "disabled",
  mock_oa_state: "ready",
};

function jsonResponse(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
function errorResponse(status: number, code: string) {
  return jsonResponse({ error: { code, message: code }, request_id: "request-1" }, status);
}
