import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClient } from "./client";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ApiClient", () => {
  it("uses the same-origin API prefix and includes credentials", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: "ok" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await new ApiClient().request<{ status: string }>("health");

    expect(result).toEqual({ status: "ok" });
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/health",
      expect.objectContaining({ credentials: "same-origin" }),
    );
  });

  it("converts the structured error envelope without logging sensitive data", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        JSON.stringify({
          error: { code: "not_found", message: "未找到记录" },
          request_id: "request-42",
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const request = new ApiClient().request("employees/unknown");

    await expect(request).rejects.toMatchObject({
      status: 404,
      code: "not_found",
      message: "未找到记录",
      requestId: "request-42",
    });
  });
});
