import React from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/manrope";
import { App } from "./App";
import "./styles.css";

class AppBoundary extends React.Component<
  React.PropsWithChildren,
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <main className="startup">
        <h1>Не удалось показать интерфейс</h1>
        <p>
          Ранее сохранённые PDF остаются в папке результата. Несохранённые правки
          могли не сохраниться. Перезапустите приложение и откройте результат из истории.
        </p>
        <button onClick={() => location.reload()}>
          Перезагрузить интерфейс
        </button>
      </main>
    ) : (
      this.props.children
    );
  }
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <AppBoundary>
      <App />
    </AppBoundary>
  </React.StrictMode>,
);
