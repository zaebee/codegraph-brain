import { useCallback } from "react";
import { toPng, toSvg } from "html-to-image";

/**
 * Хук для экспорта графа в PNG/SVG.
 * @param {Function} downloadCallback - функция для скачивания (dataUrl, filename)
 * @returns {{ exportPng: Function, exportSvg: Function }}
 */
export function useExport(downloadCallback: (dataUrl: string, filename: string) => void) {
  /**
   * Export viewport as PNG
   */
  const exportPng = useCallback(() => {
    const el = document.querySelector(".react-flow__viewport") as HTMLElement | null;
    if (!el) return;

    toPng(el, {
      backgroundColor: "#0d1117",
      pixelRatio: 2,
      filter: (node) =>
        !node?.classList?.contains("react-flow__minimap") &&
        !node?.classList?.contains("react-flow__controls"),
    })
      .then((dataUrl) => downloadCallback(dataUrl, "graph.png"))
      .catch((err) => console.error("PNG export failed:", err));
  }, [downloadCallback]);

  /**
   * Export viewport as SVG
   */
  const exportSvg = useCallback(() => {
    const el = document.querySelector(".react-flow__viewport") as HTMLElement | null;
    if (!el) return;

    toSvg(el, {
      backgroundColor: "#0d1117",
      filter: (node) =>
        !node?.classList?.contains("react-flow__minimap") &&
        !node?.classList?.contains("react-flow__controls"),
    })
      .then((dataUrl) => downloadCallback(dataUrl, "graph.svg"))
      .catch((err) => console.error("SVG export failed:", err));
  }, [downloadCallback]);

  return { exportPng, exportSvg };
}
