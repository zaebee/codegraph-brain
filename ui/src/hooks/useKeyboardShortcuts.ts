import { useEffect, useCallback } from "react";

/**
 * Хук для обработки клавиатурных шорткратов.
 * @param {Function} onEscape - callback для Esc
 * @param {Function} onFit - callback для F
 * @param {Function} onFocusSearch - callback для /
 * @param {boolean} disabled - отключить шорткаты (например, когда input в фокусе)
 */
export function useKeyboardShortcuts({
  onEscape,
  onFit,
  onFocusSearch,
  disabled = false,
}: {
  onEscape?: () => void;
  onFit?: () => void;
  onFocusSearch?: () => void;
  disabled?: boolean;
}) {
  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (disabled) return;
      if (document.activeElement?.tagName === "INPUT") return;

      switch (e.key) {
        case "Escape":
          e.preventDefault();
          onEscape?.();
          break;
        case "f":
        case "F":
          e.preventDefault();
          onFit?.();
          break;
        case "/":
          e.preventDefault();
          onFocusSearch?.();
          break;
        default:
          break;
      }
    },
    [disabled, onEscape, onFit, onFocusSearch]
  );

  useEffect(() => {
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [handleKeyDown]);
}
