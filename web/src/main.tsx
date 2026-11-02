import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
// Familjen Grotesk for everything: its default figures are tabular, so readings line up
// without a second face
import "@fontsource-variable/familjen-grotesk";
import "./styles/app.css";
import { applyTheme, useStore } from "./lib/store";
import { loadLang } from "./lib/i18n/core";

applyTheme(useStore.getState().theme);

// The applicant's portal (#/apply) is its own small page: the branch is taken before the
// application is imported, so a household never downloads the map or the chart library.
const portal = window.location.hash.startsWith("#/apply");
const root = portal ? import("./apply/Apply") : import("./App");

// The stored language loads before the first render: a Swedish reader never sees the page in
// English for a moment. If its chunk cannot load (offline), the interface opens in English.
try {
  await loadLang(useStore.getState().lang);
} catch {
  useStore.setState({ lang: "en" });
}
document.documentElement.lang = useStore.getState().lang;
const { default: Root } = await root;

const client = new QueryClient({ defaultOptions: { queries: { refetchOnWindowFocus: false } } });

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <Root />
    </QueryClientProvider>
  </StrictMode>,
);
