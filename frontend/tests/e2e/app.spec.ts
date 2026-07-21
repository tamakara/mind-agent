import { expect, type Page, type Route, test } from "@playwright/test";

test("administrator login exposes only the fixed management menu", async ({ page }) => {
  const backend = await mockBackend(page, false);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "管理员登录" })).toBeVisible();
  await page.getByLabel("用户名").fill("admin");
  await page.getByLabel("密码").fill("password");
  await page.getByRole("button", { name: /登\s*录/ }).click();
  await expect(page.getByRole("heading", { name: "概览", level: 2 })).toBeVisible();
  for (const item of ["概览", "员工", "知识库", "MCP", "审计", "设置"]) {
    await expect(page.getByRole("menuitem").filter({ hasText: item })).toBeVisible();
  }
  await expect(page.getByText("测试聊天")).toHaveCount(0);
  await expect(page.getByText("业务审批")).toHaveCount(0);
  expect(backend.loginCalls).toBe(1);
});

test("employee creation and pending Feishu identity binding work end to end", async ({ page }) => {
  const backend = await mockBackend(page, true);
  await page.goto("/");
  await page.getByRole("menuitem").filter({ hasText: "员工" }).click();
  await expect(page.getByRole("heading", { name: "员工", level: 2 })).toBeVisible();
  await page.getByRole("button", { name: "新建员工" }).click();
  await page.getByLabel("员工号").fill("E10001");
  await page.getByLabel("姓名").fill("张三");
  await page.getByLabel("部门").fill("研发部");
  await page.locator(".ant-modal").getByRole("button").last().click();
  await expect(page.getByText("张三")).toBeVisible();
  await page.getByRole("tab", { name: /飞书身份/ }).click();
  await page.getByRole("tabpanel").getByRole("combobox").click();
  await page.getByText(/张三 · E10001/).click();
  await page.getByRole("button", { name: "绑定" }).click();
  await expect(page.getByText("已绑定", { exact: true })).toBeVisible();
  expect(backend.bindCalls).toBe(1);
});

test("knowledge editing and model configuration keep credentials masked", async ({ page }) => {
  const backend = await mockBackend(page, true);
  await page.goto("/");
  await page.getByRole("menuitem").filter({ hasText: "知识库" }).click();
  const editor = page.getByLabel("知识文档内容");
  await expect(editor).toHaveValue("# 请假制度\n旧内容");
  await editor.fill("# 请假制度\n新内容");
  await page.getByRole("button", { name: "保存" }).click();
  expect(backend.savedKnowledge).toContain("新内容");

  await page.getByRole("menuitem").filter({ hasText: "设置" }).click();
  await expect(page.getByText("密钥已配置")).toBeVisible();
  await expect(page.getByLabel("API Key")).toHaveValue("");
  await page.getByRole("textbox", { name: /模型/ }).fill("gpt-test-2");
  await page.getByRole("button", { name: "保存" }).click();
  expect(backend.savedModel).toBe("gpt-test-2");
});

async function mockBackend(page: Page, loggedIn: boolean) {
  const state = { loginCalls: 0, bindCalls: 0, savedKnowledge: "", savedModel: "" };
  let authenticated = loggedIn;
  let employees: Array<Record<string, unknown>> = [];
  let identity = {
    identity_id: "20000000-0000-4000-8000-000000000001",
    app_id: "cli_1",
    platform_user_id: "ou_1",
    display_name: "飞书用户",
    binding_status: "unbound",
    employee_id: null as string | null,
    revision: 0,
  };
  const node = {
    node_id: "30000000-0000-4000-8000-000000000001",
    parent_id: null,
    node_type: "document",
    name: "员工请假制度.md",
    relative_path: "员工请假制度.md",
    revision: 0,
    reconciliation_required: false,
    version: 1,
    content_sha256: "hash",
    index_status: "active",
    index_error_code: null,
    created_at: "2026-07-22T00:00:00Z",
    updated_at: "2026-07-22T00:00:00Z",
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    if (path.endsWith("/auth/session"))
      return authenticated
        ? json(route, session)
        : json(route, error("authentication_required"), 401);
    if (path.endsWith("/auth/login")) {
      authenticated = true;
      state.loginCalls += 1;
      return json(route, session);
    }
    if (path.endsWith("/overview")) return json(route, overview);
    if (path.endsWith("/employees") && method === "GET") return json(route, pageOf(employees));
    if (path.endsWith("/employees") && method === "POST") {
      const payload = request.postDataJSON();
      const employee = {
        ...payload,
        employee_id: "10000000-0000-4000-8000-000000000001",
        manager_employee_id: null,
        status: "active",
        revision: 0,
        created_at: "2026-07-22T00:00:00Z",
        updated_at: "2026-07-22T00:00:00Z",
      };
      employees = [employee];
      return json(route, employee, 201);
    }
    if (path.endsWith("/channel-identities")) return json(route, pageOf([identity]));
    if (path.endsWith("/bind")) {
      const payload = request.postDataJSON();
      identity = {
        ...identity,
        binding_status: "bound",
        employee_id: payload.employee_id,
        revision: 1,
      };
      state.bindCalls += 1;
      return json(route, { identity, session_id: "40000000-0000-4000-8000-000000000001" });
    }
    if (path.endsWith("/knowledge/nodes")) return json(route, [node]);
    if (path.includes("/knowledge/documents/") && method === "GET")
      return json(route, { ...node, content: "# 请假制度\n旧内容" });
    if (path.includes("/knowledge/documents/") && method === "PUT") {
      state.savedKnowledge = request.postDataJSON().content;
      return json(route, { ...node, revision: 1, version: 2 });
    }
    if (path.endsWith("/providers"))
      return json(route, [
        {
          provider_kind: "chat",
          base_url: "https://provider.example/v1",
          model: "gpt-test",
          api_key_configured: true,
          revision: 0,
          created_at: "2026-07-22T00:00:00Z",
          updated_at: "2026-07-22T00:00:00Z",
        },
      ]);
    if (path.endsWith("/providers/chat") && method === "PUT") {
      state.savedModel = request.postDataJSON().model;
      return json(route, {
        provider_kind: "chat",
        base_url: "https://provider.example/v1",
        model: state.savedModel,
        api_key_configured: true,
        revision: 1,
      });
    }
    if (path.endsWith("/feishu/status")) return json(route, { state: "disabled" });
    return json(route, error("not_found"), 404);
  });
  return state;
}

const session = { admin_user_id: "admin-1", username: "admin", expires_at: "2026-07-22T10:00:00Z" };
const overview = {
  employees_total: 0,
  employees_active: 0,
  identities_unbound: 1,
  knowledge_documents: 1,
  knowledge_index_failed: 0,
  mcp_clients: 1,
  mcp_tools_exposed: 3,
  pending_actions: 0,
  feishu_state: "disabled",
  mock_oa_state: "ready",
};
function pageOf(items: unknown[]) {
  return { items, total: items.length, limit: 100, offset: 0 };
}
function error(code: string) {
  return { error: { code, message: code }, request_id: "request-1" };
}
async function json(route: Route, body: unknown, status = 200) {
  await route.fulfill({ status, contentType: "application/json", body: JSON.stringify(body) });
}
