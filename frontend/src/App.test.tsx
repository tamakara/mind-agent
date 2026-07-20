import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the administration overview skeleton", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "概览", level: 2 })).toBeInTheDocument();
    expect(screen.getByText("WorkHub API")).toBeInTheDocument();
    expect(screen.getByText("Mock OA")).toBeInTheDocument();
    expect(screen.getByText("飞书连接")).toBeInTheDocument();
  });
});
