import { useEffect } from "react";

import { useI18n } from "./I18nContext";
import { translateUiText, type Language } from "./translations";

const translatedTextNodes = new WeakMap<Text, { original: string; rendered: string }>();
const translatedAttributes = new WeakMap<Element, Map<string, string>>();
const translatableAttributes = ["aria-label", "placeholder", "title", "alt"];

function isExcluded(node: Node) {
  const element = node.nodeType === Node.ELEMENT_NODE ? node as Element : node.parentElement;
  const boundary = element?.closest("[data-i18n-skip], [data-i18n-include], code, pre, script, style");
  return Boolean(boundary && !boundary.hasAttribute("data-i18n-include"));
}

function translateTextNode(node: Text, language: Language) {
  if (isExcluded(node)) return;
  const current = node.nodeValue ?? "";
  const previous = translatedTextNodes.get(node);
  const original = previous && current === previous.rendered ? previous.original : current;
  const leading = original.match(/^\s*/)?.[0] ?? "";
  const trailing = original.match(/\s*$/)?.[0] ?? "";
  const core = original.trim();
  if (!core) return;
  const translated = `${leading}${translateUiText(core, language)}${trailing}`;
  translatedTextNodes.set(node, { original, rendered: translated });
  if (node.nodeValue !== translated) node.nodeValue = translated;
}

function translateElementAttributes(element: Element, language: Language) {
  if (isExcluded(element)) return;
  let originals = translatedAttributes.get(element);
  if (!originals) {
    originals = new Map<string, string>();
    translatedAttributes.set(element, originals);
  }
  for (const attribute of translatableAttributes) {
    const current = element.getAttribute(attribute);
    if (current == null) continue;
    if (!originals.has(attribute)) originals.set(attribute, current);
    element.setAttribute(attribute, translateUiText(originals.get(attribute)!, language));
  }
}

function translateTree(root: Node, language: Language) {
  if (root.nodeType === Node.TEXT_NODE) {
    translateTextNode(root as Text, language);
    return;
  }
  if (root.nodeType === Node.ELEMENT_NODE) translateElementAttributes(root as Element, language);
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    if (node.nodeType === Node.TEXT_NODE) translateTextNode(node as Text, language);
    else translateElementAttributes(node as Element, language);
    node = walker.nextNode();
  }
}

export default function LocalizedDocument() {
  const { language } = useI18n();

  useEffect(() => {
    let translating = false;
    const apply = (root: Node) => {
      translating = true;
      translateTree(root, language);
      translating = false;
    };

    apply(document.body);
    const observer = new MutationObserver((mutations) => {
      if (translating) return;
      for (const mutation of mutations) {
        if (mutation.type === "characterData") apply(mutation.target);
        for (const node of mutation.addedNodes) apply(node);
      }
    });
    observer.observe(document.body, { childList: true, characterData: true, subtree: true });
    return () => observer.disconnect();
  }, [language]);

  return null;
}
