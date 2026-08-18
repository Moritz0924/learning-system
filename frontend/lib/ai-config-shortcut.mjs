export function shouldNavigateToAiConfig(event) {
  if (event.defaultPrevented || event.key !== "," || (!event.ctrlKey && !event.metaKey)) return false;
  const target = event.target;
  const tagName = target?.tagName?.toUpperCase?.();
  return !(
    tagName === "INPUT"
    || tagName === "TEXTAREA"
    || tagName === "SELECT"
    || target?.isContentEditable
    || target?.closest?.('[contenteditable="true"]')
  );
}
