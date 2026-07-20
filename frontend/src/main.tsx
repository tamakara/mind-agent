import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";

import { App } from "./App";
import "./styles.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ConfigProvider
      locale={zhCN}
      theme={{
        token: {
          borderRadius: 6,
          colorPrimary: "#176b4d",
          colorText: "#202522",
          fontFamily:
            'Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
        },
      }}
    >
      <App />
    </ConfigProvider>
  </React.StrictMode>,
);
