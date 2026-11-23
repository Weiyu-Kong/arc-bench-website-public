import { useEffect, useRef, useState } from "react";

let diagramSequence = 0;

export default function MermaidDiagram({ source, fitWidth = false }: { source: string; fitWidth?: boolean }) {
  const target = useRef<HTMLDivElement>(null);
  const [failed, setFailed] = useState(false);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme);
  useEffect(() => {
    const observer = new MutationObserver(() => setTheme(document.documentElement.dataset.theme));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    let cancelled = false;
    setFailed(false);
    const container = document.createElement("div");
    container.style.cssText = "position:absolute;left:-10000px;top:0;visibility:hidden";
    document.body.appendChild(container);
    const id = `docs-flow-${++diagramSequence}`;
    void import("mermaid").then(async ({ default: mermaid }) => {
      await document.fonts.ready;
      if (cancelled) return;
      const colors = getComputedStyle(document.documentElement);
      const color = (name: string) => colors.getPropertyValue(name).trim();
      mermaid.initialize({
        startOnLoad: false, securityLevel: "strict", theme: "base",
        // SVG labels avoid inherited prose styles changing HTML label dimensions.
        htmlLabels: false,
        fontFamily: getComputedStyle(target.current ?? document.body).fontFamily,
        flowchart: { useMaxWidth: false, padding: 24, nodeSpacing: 40, rankSpacing: 44, wrappingWidth: 320 },
        themeVariables: {
          fontSize: "16px", darkMode: theme === "dark", primaryColor: color("--bg-elevated"),
          primaryTextColor: color("--text"), primaryBorderColor: color("--border"),
          lineColor: color("--text-muted"), secondaryColor: color("--bg"),
          tertiaryColor: color("--bg"), edgeLabelBackground: color("--bg"),
        },
      });
      const { svg } = await mermaid.render(id, source, container);
      if (!cancelled && target.current) {
        target.current.innerHTML = svg;
        const diagram = target.current.querySelector("svg");
        if (diagram) {
          // The scoring overview fits its column; other diagrams keep their measured size.
          const width = diagram.viewBox.baseVal.width;
          if (width > 0) diagram.style.width = `${Math.ceil(width)}px`;
          diagram.style.maxWidth = fitWidth ? "100%" : "none";
          diagram.style.height = "auto";
        }
      }
    }).catch(() => { if (!cancelled) setFailed(true); }).finally(() => container.remove());
    return () => { cancelled = true; };
  }, [source, theme, fitWidth]);
  return failed ? <pre className="docs-diagram-fallback">{source}</pre> : <div ref={target} data-i18n-skip className="docs-diagram" role="img" aria-label="ARC-Bench execution flow" />;
}
