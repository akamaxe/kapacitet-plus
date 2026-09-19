import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/schibsted-grotesk";
import "./styles/app.css";
import App from "./App";
import { applyTheme, useStore } from "./lib/store";

applyTheme(useStore.getState().theme);
document.documentElement.lang = useStore.getState().lang;

const client = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
);
