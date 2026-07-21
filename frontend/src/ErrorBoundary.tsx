import { Alert, Button } from "antd";
import { Component, type ErrorInfo, type ReactNode } from "react";

interface State {
  failed: boolean;
}

export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { failed: false };

  static getDerivedStateFromError(): State {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Administration UI failed", error.name, info.componentStack);
  }

  render() {
    if (this.state.failed) {
      return (
        <main className="fatal-error">
          <Alert type="error" showIcon message="页面加载失败" description="请重新载入管理台。" />
          <Button onClick={() => window.location.reload()}>重新载入</Button>
        </main>
      );
    }
    return this.props.children;
  }
}
