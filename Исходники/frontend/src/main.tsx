import React from "react";
import { createRoot } from "react-dom/client";
import { LucideProvider } from "lucide-react";
import "@fontsource-variable/manrope";
import { App } from "./App";
import "./tokens.css";
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
    {/* One line weight for every icon; sizes come from the five steps 12/16/18/20/32. */}
    <LucideProvider strokeWidth={1.75}>
      <AppBoundary>
        <App />
      </AppBoundary>
    </LucideProvider>
  </React.StrictMode>,
);
