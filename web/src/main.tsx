import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// 2: Schibsted Grotesk for the interface and prose; IBM Plex Mono for readings only, so the
// weights the figures use are enough
import "@fontsource-variable/schibsted-grotesk";
import "@fontsource/ibm-plex-mono/400.css";
import "@fontsource/ibm-plex-mono/500.css";
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
