import { DeleteOutlined, PaperClipOutlined, PlusOutlined } from "@ant-design/icons";
import { message } from "antd";
import { useEffect, useRef, useState } from "react";

import DescriptionAttachmentPreview from "./DescriptionAttachmentPreview";
import { collectDescriptionAttachments, inferAttachmentKind } from "../../lib/descriptionMedia";
import { autoStructureNodeFromDescription, scenarioStepGroups, type RequirementStepGroup, type RequirementNode } from "../../lib/taskTree";
import type { SubmissionTaskAssets } from "../../lib/types";

type RequirementNodeDetailContentProps = {
  node: RequirementNode;
  mode?: "editable" | "readonly";
  presentation?: "form" | "article";
  onNodeChange?: (updater: (node: RequirementNode) => RequirementNode) => void;
  onNodeIdChange?: (nextId: string) => void;
  taskAssets?: SubmissionTaskAssets | null;
  onDescriptionAttachmentUpload?: (file: File) => Promise<string>;
  onDescriptionAttachmentDelete?: (relativePath: string) => Promise<void>;
  dependencyOptions?: Array<{ id: string; name: string }>;
  showTypeField?: boolean;
  highlightedScenario?: string | null;
};

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeAttachmentPath(rawPath: string) {
  return rawPath.trim().replace(/^\.\//, "");
}

function removeAttachmentReferences(description: string, rawPath: string) {
  const normalizedPath = normalizeAttachmentPath(rawPath);
  const pathPattern = escapeRegExp(normalizedPath);
  const optionalDotSlashPathPattern = normalizedPath.startsWith("reference/") || normalizedPath.startsWith("assets/")
    ? `(?:\\./)?${pathPattern}`
    : pathPattern;

  return description
    .replace(new RegExp(`!?\\[[^\\]]*\\]\\(${optionalDotSlashPathPattern}\\)`, "g"), "")
    .replace(new RegExp(`^\\s*${optionalDotSlashPathPattern}\\s*$`, "gm"), "")
    .replace(/[ \t]+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function ScenarioSteps({ scenario }: { scenario: RequirementNode["scenarios"][number] }) {
  return <div className="scenario-steps">{scenarioStepGroups(scenario).map((step) => (
    <section key={step.id} className="scenario-step-group">
      <strong>{step.id}</strong>
      {["Given", "When", "Then"].map((key) => <div key={key} className="scenario-step-readonly"><span className="scenario-step-keyword">{key}</span><div className="scenario-step-body">{step[key as "Given" | "When" | "Then"] || "—"}</div></div>)}
      {step.references.length > 0 ? <p className="scenario-step-references"><strong>References:</strong> {step.references.map((reference) => <code key={reference}>{reference} </code>)}</p> : null}
    </section>
  ))}</div>;
}

export default function RequirementNodeDetailContent({
  node,
  mode = "readonly",
  presentation = "form",
  onNodeChange,
  onNodeIdChange,
  taskAssets = null,
  onDescriptionAttachmentUpload,
  onDescriptionAttachmentDelete,
  dependencyOptions = [],
  showTypeField = true,
  highlightedScenario = null,
}: RequirementNodeDetailContentProps) {
  const editable = mode === "editable";
  const descriptionValue = node.description || "No description available.";
  const descriptionAttachments = collectDescriptionAttachments(node.description || "", taskAssets, node.images);
  const descriptionInputRef = useRef<HTMLTextAreaElement | null>(null);
  const descriptionAttachmentInputRef = useRef<HTMLInputElement | null>(null);
  const dependencyPickerRef = useRef<HTMLDivElement | null>(null);
  const [deletingAttachmentPath, setDeletingAttachmentPath] = useState<string | null>(null);
  const [dependencyPickerOpen, setDependencyPickerOpen] = useState(false);
  const availableDependencyOptions = dependencyOptions.filter((option) => option.id !== node.id);

  const updateNode = (updater: (node: RequirementNode) => RequirementNode) => {
    if (!editable || !onNodeChange) {
      return;
    }
    onNodeChange(updater);
  };

  useEffect(() => {
    setDependencyPickerOpen(false);
  }, [node.id]);

  useEffect(() => {
    const closeDependencyPicker = (event: MouseEvent) => {
      if (!dependencyPickerRef.current?.contains(event.target as Node)) {
        setDependencyPickerOpen(false);
      }
    };
    document.addEventListener("mousedown", closeDependencyPicker);
    return () => document.removeEventListener("mousedown", closeDependencyPicker);
  }, []);

  const addDependency = (dependencyId: string) => {
    if (!dependencyId || node.dependencies.includes(dependencyId)) {
      return;
    }
    updateNode((currentNode) => ({
      ...currentNode,
      dependencies: [...currentNode.dependencies, dependencyId],
    }));
    setDependencyPickerOpen(false);
  };

  const insertDescriptionAttachment = async (file: File) => {
    if (!editable || !onDescriptionAttachmentUpload) {
      return;
    }
    try {
      const relativePath = await onDescriptionAttachmentUpload(file);
      if (inferAttachmentKind(file.name) === "image") {
        let number = (node.images?.length ?? 0) + 1;
        while (node.images?.some((image) => image.id === `IMG-${node.id}-${number}`)) number += 1;
        const id = `IMG-${node.id}-${number}`;
        const page = file.name.replace(/\.[^.]+$/, "");
        updateNode((current) => ({ ...current, images: [...(current.images ?? []), { id, page, path: relativePath, label: page }] }));
        return;
      }
      const markdownToken = inferAttachmentKind(file.name) === "image"
        ? `![${file.name}](${relativePath})`
        : `[${file.name}](${relativePath})`;
      const textarea = descriptionInputRef.current;
      const currentDescription = node.description || "";
      const selectionStart = textarea?.selectionStart ?? currentDescription.length;
      const selectionEnd = textarea?.selectionEnd ?? currentDescription.length;
      const needsLeadingNewline = selectionStart > 0 && currentDescription[selectionStart - 1] !== "\n";
      const prefix = currentDescription.slice(0, selectionStart);
      const suffix = currentDescription.slice(selectionEnd);
      const insertion = `${needsLeadingNewline ? "\n" : ""}${markdownToken}`;
      updateNode((currentNode) => ({
        ...currentNode,
        description: `${prefix}${insertion}${suffix}`,
      }));
      window.setTimeout(() => {
        const input = descriptionInputRef.current;
        if (!input) {
          return;
        }
        const caret = prefix.length + insertion.length;
        input.focus();
        input.setSelectionRange(caret, caret);
      }, 0);
    } catch (error) {
      message.error((error as Error).message);
    }
  };

  const removeDescriptionAttachment = async (rawPath: string) => {
    if (!editable) {
      return;
    }
    const nextDescription = removeAttachmentReferences(node.description || "", rawPath);
    const normalizedPath = normalizeAttachmentPath(rawPath);
    const managedReferencePath = normalizedPath.replace(/^reference\//, "");
    try {
      setDeletingAttachmentPath(normalizedPath);
      if (normalizedPath.startsWith("reference/") && onDescriptionAttachmentDelete) {
        await onDescriptionAttachmentDelete(managedReferencePath);
      }
      updateNode((currentNode) => ({
        ...currentNode,
        description: nextDescription,
      }));
      message.success("Attachment deleted.");
    } catch (error) {
      message.error((error as Error).message);
    } finally {
      setDeletingAttachmentPath((current) => (current === normalizedPath ? null : current));
    }
  };

  const removeDeclaredImage = (rawPath: string) => {
    if (!editable) {
      return;
    }
    const normalizedPath = normalizeAttachmentPath(rawPath);
    updateNode((currentNode) => ({
      ...currentNode,
      images: (currentNode.images ?? []).filter((image) => normalizeAttachmentPath(image.path) !== normalizedPath),
    }));
    message.success("Declared image removed.");
  };

  if (!editable) {
    const scenarios = node.scenarios.length ? node.scenarios.map((scenario, index) => (
      <section key={scenario.id ?? index} className={`scenario-card${(highlightedScenario === scenario.id || highlightedScenario === `${node.id}::scenario-${index + 1}`) ? " scenario-card-highlighted" : ""}`}>
        <header className="scenario-card-top"><strong>{scenario.name}</strong><span>{scenarioStepGroups(scenario).length} steps</span></header>
        {scenario.id ? <p><code>{scenario.id}</code></p> : null}
        {scenario.steps.length ? <ScenarioSteps scenario={scenario} /> : <p className="scenario-empty-copy">No steps defined.</p>}
      </section>
    )) : <p className="submission-requirement-empty">No scenarios available.</p>;
    const attachments = descriptionAttachments.length ? <section className="requirement-detail-image-section"><h3>Description images and attachments</h3><div className="requirement-detail-image-grid">{descriptionAttachments.map((attachment, index) => <DescriptionAttachmentPreview key={index} attachment={attachment} />)}</div></section> : null;
    const dependencies = node.dependencies.length ? node.dependencies.map((id) => <span key={id} className="dependency-chip">{id}</span>) : <span className="dependency-empty-copy">No dependencies</span>;
    if (presentation === "article") return <article className="submission-requirement-article">
      <div className="submission-requirement-reading"><header className="submission-requirement-article-heading"><span>{node.id} · {node.type}</span><h2>{node.name}</h2></header>
        <section className="submission-requirement-description"><h3>Description</h3><div style={{ whiteSpace: "pre-wrap" }}>{descriptionValue}</div></section>
        <section className="submission-requirement-scenarios"><h3>Scenarios <span>{node.scenarios.length}</span></h3>{scenarios}</section>
      </div>
      <aside className="submission-requirement-context"><section><h3>Dependencies</h3><div className="dependency-chip-list readonly">{dependencies}</div></section>{attachments}</aside>
    </article>;
    return <>
      <div className="create-task-detail-grid"><label className="field-stack"><span>Requirement ID</span><input className="text-input" value={node.id} readOnly /></label><label className="field-stack"><span>Title</span><input className="text-input" value={node.name} readOnly /></label>
        {showTypeField ? <label className="field-stack"><span>Type</span><input className="text-input" value={node.type} readOnly /></label> : null}
        <label className="field-stack"><span>Dependencies</span><div className="dependency-chip-list readonly">{dependencies}</div></label>
      </div>
      <section className="field-stack"><h3>Description</h3><div className="requirement-description-readonly" style={{ whiteSpace: "pre-wrap" }}>{descriptionValue}</div></section>
      {attachments}<section className="scenario-editor"><h3>Scenarios</h3>{scenarios}</section>
    </>;
  }

  return (
    <>
      <div className="create-task-detail-grid">
        <label className="field-stack">
          <span>Requirement ID</span>
          <input
            className="text-input"
            value={node.id}
            readOnly={!editable}
            onChange={(event) => {
              const nextId = event.target.value;
              updateNode((currentNode) => ({ ...currentNode, id: nextId }));
              onNodeIdChange?.(nextId);
            }}
          />
        </label>

        <label className="field-stack">
          <span>Title</span>
          <input
            className="text-input"
            value={node.name}
            readOnly={!editable}
            onChange={(event) => updateNode((currentNode) => ({ ...currentNode, name: event.target.value }))}
          />
        </label>

        {showTypeField ? (
          <label className="field-stack">
            <span>Type</span>
            <input className="text-input" value={node.type} readOnly />
          </label>
        ) : null}

        <label className="field-stack" style={{ gridColumn: "1 / -1" }}>
          <span>Dependencies</span>
          <div ref={dependencyPickerRef} className={`dependency-picker${dependencyPickerOpen ? " open" : ""}`}>
            <button
              type="button"
              className="dependency-picker-trigger"
              aria-haspopup="listbox"
              aria-expanded={dependencyPickerOpen}
              onClick={() => setDependencyPickerOpen((current) => !current)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  setDependencyPickerOpen(false);
                }
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setDependencyPickerOpen((current) => !current);
                }
              }}
            >
              <span>Select dependency to add...</span>
              <span className="dependency-picker-chevron" aria-hidden="true" />
            </button>
            {dependencyPickerOpen ? (
              <div className="dependency-picker-menu" role="listbox" aria-label="Available dependencies">
                {availableDependencyOptions.length > 0 ? availableDependencyOptions.map((option) => {
                  const alreadySelected = node.dependencies.includes(option.id);
                  return (
                    <button
                      key={option.id}
                      type="button"
                      className="dependency-picker-option"
                      role="option"
                      aria-selected={alreadySelected}
                      disabled={alreadySelected}
                      onClick={() => addDependency(option.id)}
                    >
                      <strong>{option.id}</strong>
                      <span>{option.name}</span>
                      {alreadySelected ? <em>Added</em> : null}
                    </button>
                  );
                }) : (
                  <div className="dependency-picker-empty">No available dependencies</div>
                )}
              </div>
            ) : null}
          </div>
          <div className="dependency-chip-list">
            {node.dependencies.length > 0 ? node.dependencies.map((dependencyId) => (
              <span key={`${node.id}-${dependencyId}`} className="dependency-chip">
                <span>{dependencyId}</span>
                <button
                  type="button"
                  className="dependency-chip-remove"
                  onClick={() =>
                    updateNode((currentNode) => ({
                      ...currentNode,
                      dependencies: currentNode.dependencies.filter((item) => item !== dependencyId),
                    }))
                  }
                  aria-label={`Remove dependency ${dependencyId}`}
                >
                  x
                </button>
              </span>
            )) : (
              <span className="dependency-empty-copy">No dependencies selected</span>
            )}
          </div>
        </label>
      </div>

      <div className="field-stack">
        <span>Description</span>
        <div className="requirement-detail-description-actions">
          <button
            type="button"
            className="mini-btn detail-toolbar-like-btn"
            onClick={() => descriptionAttachmentInputRef.current?.click()}
            disabled={!editable || !onDescriptionAttachmentUpload}
          >
            <PaperClipOutlined /> Upload Attachment
          </button>
          <input
            ref={descriptionAttachmentInputRef}
            className="visually-hidden"
            type="file"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                void insertDescriptionAttachment(file);
              }
              event.currentTarget.value = "";
            }}
          />
        </div>
        <textarea
          ref={descriptionInputRef}
          className="text-area"
          rows={4}
          value={node.description}
          readOnly={!editable}
          onChange={(event) => updateNode((currentNode) => ({ ...currentNode, description: event.target.value }))}
          onBlur={(event) => updateNode((currentNode) => autoStructureNodeFromDescription(currentNode, event.target.value))}
        />
      </div>

      {(node.images?.length ?? 0) > 0 ? <section className="field-stack"><strong>Description images</strong>{node.images?.map((image, index) => <div key={index} className="requirement-image-fields">{(["id", "path", "page"] as const).map((key) => <label key={key} className="field-stack"><span>{key === "path" ? "reference" : key}</span><input className="text-input" value={image[key] ?? ""} onChange={(event) => updateNode((current) => ({ ...current, images: current.images?.map((item, imageIndex) => imageIndex === index ? { ...item, [key]: event.target.value, ...(key === "page" ? { label: event.target.value } : {}) } : item) }))} /></label>)}</div>)}</section> : null}

      {descriptionAttachments.length > 0 ? (
        <div className="requirement-detail-image-section">
          <div className="requirement-detail-image-title">Reference Attachments</div>
          <div className="requirement-detail-image-grid">
            {descriptionAttachments.map((attachment, index) => (
              <DescriptionAttachmentPreview
                key={`${node.id}-editable-attachment-${index}`}
                attachment={attachment}
                onDelete={attachment.source === "images"
                  ? () => removeDeclaredImage(attachment.rawPath)
                  : () => void removeDescriptionAttachment(attachment.rawPath)}
                deleting={attachment.source === "images" ? false : deletingAttachmentPath === normalizeAttachmentPath(attachment.rawPath)}
              />
            ))}
          </div>
        </div>
      ) : null}

      <div className="scenario-editor compact">
        <div className="scenario-editor-head">
          <strong>{node.scenarios.length} scenarios</strong>
          {editable ? (
            <button
              type="button"
              className="mini-btn detail-toolbar-like-btn"
              onClick={() =>
                updateNode((currentNode) => ({
                  ...currentNode,
                  scenarios: [...currentNode.scenarios, { id: `SCN-${currentNode.id}-${crypto.randomUUID().slice(0, 8)}`, name: `Scenario ${currentNode.scenarios.length + 1}`, steps: [] }],
                }))
              }
            >
              <PlusOutlined /> Add Scenario
            </button>
          ) : null}
        </div>

        {node.scenarios.map((scenario, scenarioIndex) => (
          <div key={`${node.id}-scenario-${scenarioIndex}`} className="scenario-card">
            <label className="field-stack"><span>Scenario ID</span><input className="text-input" value={scenario.id ?? `SCN-${node.id}-${String(scenarioIndex + 1).padStart(2, "0")}`} onChange={(event) => updateNode((current) => ({ ...current, scenarios: current.scenarios.map((item, index) => index === scenarioIndex ? { ...item, id: event.target.value } : item) }))} /></label>
            <div className="scenario-card-top">
              <input
                className="text-input"
                value={scenario.name}
                readOnly={!editable}
                onChange={(event) =>
                  updateNode((currentNode) => ({
                    ...currentNode,
                    scenarios: currentNode.scenarios.map((item, index) =>
                      index === scenarioIndex ? { ...item, name: event.target.value } : item,
                    ),
                  }))
                }
              />
              {editable ? (
                <button
                  type="button"
                  className="icon-only-btn"
                  onClick={() =>
                    updateNode((currentNode) => ({
                      ...currentNode,
                      scenarios: currentNode.scenarios.filter((_, index) => index !== scenarioIndex),
                    }))
                  }
                >
                  <DeleteOutlined />
                </button>
              ) : null}
            </div>

            <div className="scenario-steps">
              {scenarioStepGroups(scenario, scenario.id ?? `SCN-${node.id}-${String(scenarioIndex + 1).padStart(2, "0")}`).map((step, stepIndex) => {
                const updateGroup = (change: Partial<RequirementStepGroup>) => updateNode((current) => ({
                  ...current, scenarios: current.scenarios.map((item, index) => index !== scenarioIndex ? item : {
                    ...item, steps: scenarioStepGroups(item, item.id ?? `SCN-${node.id}-${String(scenarioIndex + 1).padStart(2, "0")}`).flatMap((group, groupIndex) => {
                      const next = groupIndex === stepIndex ? { ...group, ...change } : group;
                      return (["Given", "When", "Then"] as const).map((key) => ({ id: next.id, keyword: key.toUpperCase(), content: next[key], references: next.references }));
                    }),
                  }),
                }));
                return <section key={stepIndex} className="scenario-step-group">
                  <div className="scenario-card-top"><label className="field-stack"><span>Step ID</span><input className="text-input" value={step.id} onChange={(event) => updateGroup({ id: event.target.value })} /></label>
                    <button type="button" className="icon-only-btn" aria-label={`Remove step ${step.id}`} onClick={() => updateNode((current) => ({ ...current, scenarios: current.scenarios.map((item, index) => index !== scenarioIndex ? item : { ...item, steps: scenarioStepGroups(item, item.id ?? `SCN-${node.id}-${String(scenarioIndex + 1).padStart(2, "0")}`).filter((_, groupIndex) => groupIndex !== stepIndex).flatMap((group) => (["Given", "When", "Then"] as const).map((key) => ({ id: group.id, keyword: key.toUpperCase(), content: group[key], references: group.references }))) }) }))}><DeleteOutlined /></button>
                  </div>
                  {(["Given", "When", "Then"] as const).map((key) => <label key={key} className="field-stack"><span>{key}</span><textarea className="text-area" rows={2} value={step[key]} onChange={(event) => updateGroup({ [key]: event.target.value })} /></label>)}
                  <label className="field-stack"><span>References (one per line)</span><textarea className="text-area" rows={2} value={step.references.join("\n")} placeholder={String.raw`\ref[image](IMG-REQ-1-1)`} onChange={(event) => updateGroup({ references: event.target.value.split("\n").map((value) => value.trim()).filter(Boolean) })} /></label>
                </section>;
              })}
            </div>
            <button type="button" className="mini-btn" onClick={() => updateNode((current) => ({ ...current, scenarios: current.scenarios.map((item, index) => {
              if (index !== scenarioIndex) return item;
              const groups = scenarioStepGroups(item);
              const scenarioId = item.id ?? `SCN-${current.id}-${String(index + 1).padStart(2, "0")}`;
              let number = groups.length + 1;
              while (groups.some((group) => group.id === `STEP-${scenarioId.replace(/^SCN-/, "")}-${String(number).padStart(2, "0")}`)) number += 1;
              const id = `STEP-${scenarioId.replace(/^SCN-/, "")}-${String(number).padStart(2, "0")}`;
              return { ...item, steps: [...item.steps, ...["GIVEN", "WHEN", "THEN"].map((keyword) => ({ id, keyword, content: "", references: [] }))] };
            }) }))}><PlusOutlined /> Add Given / When / Then Step</button>
          </div>
        ))}
      </div>
    </>
  );
}
