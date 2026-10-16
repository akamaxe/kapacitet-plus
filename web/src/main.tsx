import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@fontsource-variable/ibm-plex-sans";
// 2: monospace is for data only, so the weights the figures use are enough
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
import "./styles/app.css";
import "./styles/shell.css";
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
