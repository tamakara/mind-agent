import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { McpPage } from "./McpPage";

describe("McpPage", () => {
  afterEach(() => vi.restoreAllMocks());

  it("shows clients and keeps allowlist separate from execution policy", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      const payload = url.endsWith("/mcp/clients")
        ? [
            {
              client_id: "client-1",
              client_key: "oa",
              name: "Mock OA",
              url: "http://127.0.0.1:9001/mcp",
              headers_configured: true,
              enabled: true,
              revision: 0,
            },
          ]
        : [
            {
              tool_id: "tool-1",
              client_key: "oa",
              original_name: "submit_leave_request",
              model_name: "mcp__oa__submit_leave_request",
              description: "提交年假申请",
              allowlisted: true,
              effect: "confirm",
              revision: 1,
            },
          ];
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    });

    render(<McpPage />);

    expect(await screen.findByText("Mock OA")).toBeInTheDocument();
    expect(screen.getByText("submit_leave_request")).toBeInTheDocument();
    expect(screen.getByRole("switch", { name: "submit_leave_request 模型白名单" })).toBeChecked();
    expect(
      screen.getByRole("combobox", { name: "submit_leave_request 执行策略" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2));
  });
});
