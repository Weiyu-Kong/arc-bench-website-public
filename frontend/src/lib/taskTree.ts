export type RequirementStep = {
  keyword: string;
  content: string;
  id?: string;
  references?: string[];
  actor?: string;
};

export type RequirementScenario = {
  id?: string;
  name: string;
  actor?: string;
  steps: RequirementStep[];
};

export type RequirementImage = {
  id?: string;
  page?: string;
  label: string;
  path: string;
};

export type RequirementReference = {
  label: string;
  path: string;
};

export type RequirementRole = {
  id: string;
  name: string;
};

export type RequirementNode = {
  id: string;
  name: string;
  type: string;
  description: string;
  dependencies: string[];
  children: RequirementNode[];
  scenarios: RequirementScenario[];
  images?: RequirementImage[];
  reference?: RequirementReference[];
  roles?: RequirementRole[];
  permissions?: "ALL" | string[];
};

type UnknownRecord = Record<string, unknown>;

type LineToken = {
  indent: number;
  text: string;
};

function isRecord(value: unknown): value is UnknownRecord {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function quoteYaml(value: string): string {
  return JSON.stringify(value.replaceAll("\r\n", "\n"));
}

function parseScalar(raw: string): unknown {
  const value = raw.trim();
  if (!value) {
    return "";
  }
  if (value === "[]") {
    return [];
  }
  if (value === "{}") {
    return {};
  }
  if (value === "true") {
    return true;
  }
  if (value === "false") {
    return false;
  }
  if (/^-?\d+(\.\d+)?$/.test(value)) {
    return Number(value);
  }
  if (value.startsWith("'") && value.endsWith("'")) {
    return value
      .slice(1, -1)
      .replaceAll("''", "'")
      .replaceAll("\\n", "\n")
      .replaceAll("\\\\", "\\");
  }
  if (value.startsWith('"') && value.endsWith('"')) {
    try { return JSON.parse(value); } catch { return value.slice(1, -1); }
  }
  return value;
}

function isBlockScalarIndicator(value: string): boolean {
  return /^[>|][+-]?$/.test(value.trim());
}

function collectInlineScalar(
  tokens: LineToken[],
  startIndex: number,
  parentIndent: number,
  firstLine: string,
): [unknown, number] {
  const lines = [firstLine];
  let index = startIndex;
  // YAML folds indented continuations of plain and quoted scalars into
  // spaces. Consume them before parsing so closing quotes on later lines
  // and text containing colons are handled as part of the same value.
  while (index < tokens.length && tokens[index].indent > parentIndent) {
    lines.push(tokens[index].text);
    index += 1;
  }
  return [parseScalar(lines.join(" ")), index];
}

function collectBlockScalar(
  tokens: LineToken[],
  startIndex: number,
  parentIndent: number,
  indicator: string,
): [string, number] {
  const lines: string[] = [];
  let index = startIndex;

  while (index < tokens.length) {
    const token = tokens[index];
    if (token.indent <= parentIndent) {
      break;
    }
    lines.push(token.text);
    index += 1;
  }

  const normalizedIndicator = indicator.trim();
  const isFolded = normalizedIndicator.startsWith(">");
  const chompStrip = normalizedIndicator.endsWith("-");

  let content = "";
  if (isFolded) {
    content = lines.join(" ").replace(/\s+/g, " ").trim();
  } else {
    content = lines.join("\n");
  }

  if (!chompStrip && content) {
    content += "\n";
  }

  return [content, index];
}

function tokenizeYaml(input: string): LineToken[] {
  return input
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line) => ({
      indent: line.match(/^ */)?.[0].length ?? 0,
      text: line.trimEnd(),
    }))
    .filter((line) => line.text.trim().length > 0 && !line.text.trimStart().startsWith("#"))
    .map((line) => ({
      indent: line.indent,
      text: line.text.trim(),
    }));
}

function parseBlock(tokens: LineToken[], startIndex: number, indent: number): [unknown, number] {
  if (startIndex >= tokens.length) {
    return [{}, startIndex];
  }
  const token = tokens[startIndex];
  if (token.text.startsWith("- ")) {
    return parseArray(tokens, startIndex, indent);
  }
  return parseObject(tokens, startIndex, indent);
}

function parseObject(tokens: LineToken[], startIndex: number, indent: number): [UnknownRecord, number] {
  const result: UnknownRecord = {};
  let index = startIndex;

  while (index < tokens.length) {
    const token = tokens[index];
    if (token.indent < indent) {
      break;
    }
    if (token.indent > indent) {
      index += 1;
      continue;
    }
    if (token.text.startsWith("- ")) {
      break;
    }

    const separator = token.text.indexOf(":");
    if (separator === -1) {
      index += 1;
      continue;
    }

    const key = token.text.slice(0, separator).trim();
    const rawValue = token.text.slice(separator + 1).trim();

    if (!rawValue) {
      const nextToken = tokens[index + 1];
      const isIndentlessArray = nextToken?.indent === indent && nextToken.text.startsWith("- ");
      if (nextToken && (nextToken.indent > indent || isIndentlessArray)) {
        const [nested, nextIndex] = parseBlock(tokens, index + 1, nextToken.indent);
        result[key] = nested;
        index = nextIndex;
        continue;
      }
      result[key] = {};
      index += 1;
      continue;
    }

    if (isBlockScalarIndicator(rawValue)) {
      const [blockValue, nextIndex] = collectBlockScalar(tokens, index + 1, indent, rawValue);
      result[key] = blockValue;
      index = nextIndex;
      continue;
    }

    const [scalarValue, nextIndex] = collectInlineScalar(tokens, index + 1, indent, rawValue);
    result[key] = scalarValue;
    index = nextIndex;
  }

  return [result, index];
}

function parseArray(tokens: LineToken[], startIndex: number, indent: number): [unknown[], number] {
  const items: unknown[] = [];
  let index = startIndex;

  while (index < tokens.length) {
    const token = tokens[index];
    if (token.indent < indent) {
      break;
    }
    if (token.indent !== indent || !token.text.startsWith("- ")) {
      break;
    }

    const rest = token.text.slice(2).trim();
    if (!rest) {
      if (index + 1 < tokens.length && tokens[index + 1].indent > indent) {
        const [nested, nextIndex] = parseBlock(tokens, index + 1, tokens[index + 1].indent);
        items.push(nested);
        index = nextIndex;
        continue;
      }
      items.push("");
      index += 1;
      continue;
    }

    if (rest.includes(":")) {
      const separator = rest.indexOf(":");
      const key = rest.slice(0, separator).trim();
      const rawValue = rest.slice(separator + 1).trim();
      const item: UnknownRecord = {};
      if (rawValue && isBlockScalarIndicator(rawValue)) {
        const [blockValue, nextIndex] = collectBlockScalar(tokens, index + 1, indent, rawValue);
        item[key] = blockValue;
        index = nextIndex;
      } else {
        if (rawValue) {
          // The inline key in a sequence mapping lives two columns past
          // the dash; sibling fields at that indentation are not continuations.
          const [scalarValue, nextIndex] = collectInlineScalar(tokens, index + 1, indent + 2, rawValue);
          item[key] = scalarValue;
          index = nextIndex;
        } else {
          item[key] = "";
          index += 1;
        }
      }

      while (index < tokens.length) {
        const next = tokens[index];
        if (next.indent < indent + 2) {
          break;
        }
        if (next.indent === indent && next.text.startsWith("- ")) {
          break;
        }
        if (next.indent === indent + 2 && next.text.startsWith("- ")) {
          const [nestedArray, nextIndex] = parseArray(tokens, index, indent + 2);
          if (Array.isArray(item[key]) && item[key].length === 0) {
            item[key] = nestedArray;
          } else if (item[key] === "") {
            item[key] = nestedArray;
          }
          index = nextIndex;
          continue;
        }
        if (next.indent >= indent + 2) {
          const [nestedObject, nextIndex] = parseObject(tokens, index, indent + 2);
          Object.assign(item, nestedObject);
          index = nextIndex;
          continue;
        }
      }

      items.push(item);
      continue;
    }

    items.push(parseScalar(rest));
    index += 1;
  }

  return [items, index];
}

function asStringArray(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item ?? "").trim()).filter(Boolean);
}

function normalizeImages(value: unknown): RequirementImage[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item, index) => {
    if (isRecord(item)) {
      const path = String(item.reference ?? item.path ?? "").trim();
      if (!path) return [];
      return [{ label: [item.id, item.page ?? item.label ?? `Reference image ${index + 1}`].filter(Boolean).join(" · "), path, ...(item.id ? { id: String(item.id) } : {}), ...(item.page ? { page: String(item.page) } : {}) }];
    }
    const path = String(item ?? "").trim();
    return path ? [{ label: `Reference image ${index + 1}`, path }] : [];
  });
}

function normalizeReferences(value: unknown): RequirementReference[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item, index) => {
    if (!isRecord(item)) return [];
    const path = String(item.path ?? "").trim();
    if (!path) return [];
    return [{ label: String(item.label ?? `Reference ${index + 1}`).trim() || `Reference ${index + 1}`, path }];
  });
}

function normalizeRoles(value: unknown): RequirementRole[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.flatMap((item) => {
    if (!isRecord(item)) return [];
    const id = String(item.id ?? "").trim();
    return id ? [{ id, name: String(item.name ?? id).trim() || id }] : [];
  });
}

function normalizePermissions(value: unknown): "ALL" | string[] | undefined {
  if (typeof value === "string") {
    return value.trim().toUpperCase() === "ALL" ? "ALL" : asStringArray(value ? [value] : []);
  }
  return Array.isArray(value) ? asStringArray(value) : undefined;
}

function normalizeScenario(input: unknown, fallbackId = "SCN"): RequirementScenario {
  const source = isRecord(input) ? input : {};
  const rawSteps = Array.isArray(source.steps) ? source.steps : [];
  const actor = String(source.actor ?? "").trim();
  return {
    id: String(source.id ?? fallbackId),
    name: String(source.name ?? "New scenario").trim() || "New scenario",
    ...(actor ? { actor } : {}),
    steps: rawSteps.flatMap((raw, index) => {
      const step = isRecord(raw) ? raw : {};
      const metadata = {
        ...(step.id ? { id: String(step.id) } : {}),
        ...(step.actor ? { actor: String(step.actor) } : {}),
        references: asStringArray(step.references),
      };
      const clauses = ["Given", "When", "Then"].filter((key) => key in step);
      if (clauses.length) {
        return clauses.map((key) => ({ ...metadata, id: String(step.id ?? `STEP-${String(source.id ?? fallbackId).replace(/^SCN-/, "")}-${String(index + 1).padStart(2, "0")}`), keyword: key.toUpperCase(), content: String(step[key] ?? "") }));
      }
      return [{ ...metadata, keyword: String(step.keyword ?? "GIVEN").toUpperCase(), content: String(step.content ?? "") }];
    }),
  };
}

export type RequirementStepGroup = { id: string; Given: string; When: string; Then: string; references: string[]; actor?: string };

export function scenarioStepGroups(scenario: RequirementScenario, scenarioId = scenario.id ?? "SCN"): RequirementStepGroup[] {
  const groups: RequirementStepGroup[] = [];
  for (const step of scenario.steps) {
    let group = step.id ? groups.find((item) => item.id === step.id) : undefined;
    const keyword = step.keyword.toUpperCase();
    if (!group && !step.id && keyword !== "GIVEN") group = groups[groups.length - 1];
    if (!group) {
      group = { id: step.id ?? `STEP-${scenarioId.replace(/^SCN-/, "")}-${String(groups.length + 1).padStart(2, "0")}`, Given: "", When: "", Then: "", references: [], ...(step.actor ? { actor: step.actor } : {}) };
      groups.push(group);
    }
    const field = keyword === "WHEN" ? "When" : keyword === "THEN" || keyword === "AND" && group.Then ? "Then" : keyword === "AND" && group.When ? "When" : "Given";
    group[field] = [group[field], step.content].filter(Boolean).join("\n");
    group.references = [...new Set([...group.references, ...(step.references ?? [])])];
  }
  return groups;
}

function normalizeNode(input: unknown, fallbackId: string): RequirementNode {
  const source = isRecord(input) ? input : {};
  const type = String(source.type ?? (Array.isArray(source.children) && source.children.length ? "FOLDER" : "ATOMIC")).trim().toUpperCase();
  const description = isRecord(source.description) ? source.description : { text: source.description };
  const children = Array.isArray(source.children)
    ? source.children.map((child, index) => normalizeNode(child, `${fallbackId}-${index + 1}`))
    : [];
  const scenarios = Array.isArray(source.scenarios) ? source.scenarios.map((scenario, index) => normalizeScenario(scenario, `SCN-${String(source.id ?? fallbackId)}-${String(index + 1).padStart(2, "0")}`)) : [];

  return {
    id: String(source.id ?? fallbackId).trim() || fallbackId,
    name: String(source.name ?? fallbackId).trim() || fallbackId,
    type,
    description: String(description.text ?? "").trim(),
    dependencies: [...new Set([...asStringArray(source.dependencies), ...scenarios.flatMap((scenario) => scenario.steps.flatMap((step) => (step.references ?? []).flatMap((reference) => [...reference.matchAll(/\\ref\[requirement\]\(([^)]+)\)/g)].map((match) => match[1]))))])].filter((id) => id !== String(source.id ?? fallbackId)),
    children,
    scenarios,
    images: normalizeImages([
      ...(Array.isArray(description.image) ? description.image : []),
      ...(Array.isArray(source.images) ? source.images : []),
      ...(Array.isArray(source.pages) ? source.pages.map((page) => isRecord(page) ? { ...page, page: page.name } : page) : []),
    ]),
    reference: normalizeReferences(source.reference),
    roles: normalizeRoles(source.roles),
    permissions: normalizePermissions(source.permissions),
  };
}

export function createDefaultTaskTree(): RequirementNode {
  return {
    id: "ROOT",
    name: "New Requirement Tree",
    type: "FOLDER",
    description: "Describe the product goal, business scope, and the user value that this task should cover.",
    dependencies: [],
    children: [
      {
        id: "REQ-1",
        name: "Core Experience",
        type: "ATOMIC",
        description: "Describe the first end-to-end behavior that the target application must support.",
        dependencies: [],
        children: [],
        scenarios: [
          {
            id: "SCN-REQ-1-01",
            name: "Primary happy path",
            steps: [
              { id: "STEP-REQ-1-01-01", keyword: "GIVEN", content: "The application is available to the user.", references: [] },
              { id: "STEP-REQ-1-01-01", keyword: "WHEN", content: "The user opens the main workflow.", references: [] },
              { id: "STEP-REQ-1-01-01", keyword: "THEN", content: "The expected result is rendered and usable.", references: [] },
            ],
          },
        ],
      },
    ],
    scenarios: [],
  };
}

export function parseTaskTreeYaml(yamlContent: string): RequirementNode {
  const tokens = tokenizeYaml(yamlContent);
  const [parsed] = parseBlock(tokens, 0, 0);
  return normalizeNode(parsed, "ROOT");
}

function parseDependenciesBlock(lines: string[]): string[] {
  const markerIndex = lines.findIndex((line) => line.trim() === "**Dependencies:**");
  if (markerIndex >= 0) {
    const collected: string[] = [];
    for (let index = markerIndex + 1; index < lines.length; index += 1) {
      const line = lines[index].trim();
      if (!line) {
        continue;
      }
      if (line.startsWith("**")) {
        break;
      }
      collected.push(line);
    }
    const raw = collected.join(" ");
    if (!raw || raw.toLowerCase() === "none") {
      return [];
    }
    return raw.split(",").map((item) => item.trim()).filter(Boolean);
  }

  const inlineLine = lines.find((line) => line.trim().startsWith("**Dependencies:**"));
  if (!inlineLine) {
    return [];
  }
  const raw = inlineLine.replace("**Dependencies:**", "").trim();
  if (!raw || raw.toLowerCase() === "none") {
    return [];
  }
  return raw.split(",").map((item) => item.trim()).filter(Boolean);
}

function parseScenariosBlock(lines: string[]): RequirementScenario[] {
  const markerIndex = lines.findIndex((line) => ["**Scenarios:**", "### Acceptance scenarios"].includes(line.trim()));
  if (markerIndex < 0) return [];
  const scenarios: RequirementScenario[] = [];
  let current: RequirementScenario | undefined;
  let stepId: string | undefined;
  for (const raw of lines.slice(markerIndex + 1)) {
    const line = raw.trim();
    if (/^#{2,3} /.test(line)) break;
    const heading = line.startsWith("#### ") ? line.slice(5) : /^- (?!\*\*)/.test(line) ? line.slice(2) : null;
    if (heading !== null) {
      const match = /^(SCN-\S+)(?:\s+—)?\s+(.*)$/.exec(heading);
      current = { ...(match ? { id: match[1] } : {}), name: match?.[2] ?? heading, steps: [] };
      scenarios.push(current); stepId = undefined; continue;
    }
    if (!current) continue;
    const step = /^-\s+\*\*(?:Step:?)[*]{2}:?\s+(.+)$/.exec(line);
    if (step) { stepId = step[1].trim(); continue; }
    const clause = /^-\s+\*\*(Given|When|Then|GIVEN|WHEN|THEN|AND):?\*\*:?[\s]+(.*)$/.exec(line);
    if (clause) { current.steps.push({ ...(stepId ? { id: stepId } : {}), keyword: clause[1].toUpperCase(), content: clause[2] }); continue; }
    if (line.startsWith("- **References:")) {
      const references = [...line.matchAll(/`([^`]+)`/g)].map((match) => match[1]);
      current.steps.filter((item) => item.id === stepId).forEach((item) => { item.references = references; });
    }
  }
  return scenarios;
}

function extractDescriptionLines(lines: string[]): string[] {
  const output: string[] = [];
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) {
      if (output.length > 0 && output[output.length - 1] !== "") {
        output.push("");
      }
      continue;
    }
    if (trimmed.startsWith("**Type:**") || trimmed === "### Acceptance scenarios") break;
    if (trimmed.startsWith("**Dependencies:**")) {
      break;
    }
    if (trimmed === "**Dependencies:**") {
      break;
    }
    if (trimmed.startsWith("**Scenarios:**")) {
      break;
    }
    output.push(trimmed);
  }
  while (output[output.length - 1] === "") {
    output.pop();
  }
  return output;
}

export function requirementMarkdownToTree(markdown: string): RequirementNode {
  const normalized = markdown.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  const titleLine = lines.find((line) => line.startsWith("# "));
  const rootTitle = titleLine ? titleLine.slice(2).trim() : "Imported Requirement";

  const root: RequirementNode = {
    id: "ROOT",
    name: rootTitle || "Imported Requirement",
    type: "FOLDER",
    description: "",
    dependencies: [],
    children: [],
    scenarios: [],
  };

  const rootBody: string[] = [];
  const nodeMap = new Map<string, RequirementNode>();
  nodeMap.set(root.id, root);

  type Section = {
    level: number;
    id: string;
    name: string;
    body: string[];
  };

  const sections: Section[] = [];
  let currentSection: Section | null = null;

  for (const line of lines) {
    if (line.startsWith("# ")) {
      continue;
    }
    const sectionMatch = /^(#{2,6})\s+(REQ-[^\s]+)\s+(.+)$/.exec(line.trim());
    if (sectionMatch) {
      if (currentSection) {
        sections.push(currentSection);
      }
      currentSection = {
        level: sectionMatch[1].length,
        id: sectionMatch[2].trim(),
        name: sectionMatch[3].trim(),
        body: [],
      };
      continue;
    }

    if (currentSection) {
      currentSection.body.push(line);
    } else {
      rootBody.push(line);
    }
  }

  if (currentSection) {
    sections.push(currentSection);
  }

  root.description = extractDescriptionLines(rootBody).join("\n").trim();

  sections.forEach((section) => {
    const node: RequirementNode = {
      id: section.id,
      name: section.name,
      type: "ATOMIC",
      description: extractDescriptionLines(section.body).join("\n").trim(),
      dependencies: parseDependenciesBlock(section.body),
      children: [],
      scenarios: parseScenariosBlock(section.body),
    };

    nodeMap.set(node.id, node);

    const parentId = section.level === 2
      ? "ROOT"
      : section.id.split(".").slice(0, -1).join(".");

    const parentNode = nodeMap.get(parentId) ?? root;
    parentNode.children.push(node);
    parentNode.type = "FOLDER";
  });

  return root;
}

function renderScenarioYaml(scenario: RequirementScenario, indent: number, scenarioId: string): string[] {
  const pad = " ".repeat(indent);
  const nested = " ".repeat(indent + 2);
  const lines = [`${pad}- id: ${quoteYaml(scenarioId)}`, `${nested}name: ${quoteYaml(scenario.name)}`];
  if (scenario.actor) lines.push(`${nested}actor: ${quoteYaml(scenario.actor)}`);
  const groups = scenarioStepGroups(scenario, scenarioId);
  if (!groups.length) return [...lines, `${nested}steps: []`];
  lines.push(`${nested}steps:`);
  for (const step of groups) {
    lines.push(`${nested}  - id: ${quoteYaml(step.id)}`);
    if (step.actor) lines.push(`${nested}    actor: ${quoteYaml(step.actor)}`);
    for (const key of ["Given", "When", "Then"] as const) lines.push(`${nested}    ${key}: ${quoteYaml(step[key])}`);
    lines.push(`${nested}    references:${step.references.length ? "" : " []"}`);
    step.references.forEach((reference) => lines.push(`${nested}      - ${quoteYaml(reference)}`));
  }
  return lines;
}

function renderNodeYaml(node: RequirementNode, indent: number, asListItem = false): string[] {
  const pad = " ".repeat(indent);
  const fieldIndent = indent + (asListItem ? 2 : 0);
  const fieldPad = " ".repeat(fieldIndent);
  const lines = [
    asListItem ? `${pad}- id: ${node.id}` : `${pad}id: ${node.id}`,
    `${fieldPad}name: ${quoteYaml(node.name)}`,
    `${fieldPad}description:`,
    `${fieldPad}  text: ${quoteYaml(node.description)}`,
  ];

  if (node.images?.length) {
    lines.push(`${fieldPad}  image:`);
    node.images.forEach((image, index) => {
      lines.push(`${fieldPad}    - id: ${quoteYaml(image.id ?? `IMG-${node.id}-${index + 1}`)}`);
      lines.push(`${fieldPad}      reference: ${quoteYaml(image.path)}`);
      lines.push(`${fieldPad}      page: ${quoteYaml(image.page ?? image.label)}`);
    });
  }

  if (node.reference && node.reference.length > 0) {
    lines.push(`${fieldPad}reference:`);
    node.reference.forEach((reference) => {
      lines.push(`${fieldPad}  - label: ${quoteYaml(reference.label)}`);
      lines.push(`${fieldPad}    path: ${quoteYaml(reference.path)}`);
    });
  }

  if (node.roles && node.roles.length > 0) {
    lines.push(`${fieldPad}roles:`);
    node.roles.forEach((role) => {
      lines.push(`${fieldPad}  - id: ${quoteYaml(role.id)}`);
      lines.push(`${fieldPad}    name: ${quoteYaml(role.name)}`);
    });
  }

  if (node.permissions) {
    if (node.permissions === "ALL") {
      lines.push(`${fieldPad}permissions: ALL`);
    } else if (node.permissions.length === 0) {
      lines.push(`${fieldPad}permissions: []`);
    } else {
      lines.push(`${fieldPad}permissions:`);
      node.permissions.forEach((permission) => lines.push(`${fieldPad}  - ${quoteYaml(permission)}`));
    }
  }

  if (node.dependencies.length === 0) {
    lines.push(`${fieldPad}dependencies: []`);
  } else {
    lines.push(`${fieldPad}dependencies:`);
    node.dependencies.forEach((dependency) => {
      lines.push(`${fieldPad}  - ${quoteYaml(dependency)}`);
    });
  }

  if (node.children.length > 0) {
    lines.push(`${fieldPad}children:`);
    node.children.forEach((child) => {
      lines.push(...renderNodeYaml(child, fieldIndent + 2, true));
    });
  } else {
    lines.push(`${fieldPad}children: []`);
  }

  if (node.scenarios.length > 0) {
    lines.push(`${fieldPad}scenarios:`);
    node.scenarios.forEach((scenario, index) => {
      lines.push(...renderScenarioYaml(scenario, fieldIndent + 2, scenario.id ?? `SCN-${node.id}-${String(index + 1).padStart(2, "0")}`));
    });
  } else {
    lines.push(`${fieldPad}scenarios: []`);
  }

  return lines;
}

export function taskTreeToYaml(node: RequirementNode): string {
  return renderNodeYaml(node, 0).join("\n").trim();
}

type MarkdownSection = {
  heading: string;
  body: string[];
};

function buildMarkdownSections(node: RequirementNode, sections: MarkdownSection[], depth: number) {
  const title = `${"#".repeat(Math.min(depth + 2, 6))} ${node.id} ${node.name}`;
  const body: string[] = [];

  if (node.description) {
    body.push(node.description);
  }
  if (node.images?.length) {
    body.push(...node.images.map((image) => `![${[image.id, image.page ?? image.label].filter(Boolean).join(" · ")}](${image.path})`));
  }
  if (node.reference?.length) {
    body.push("### References", ...node.reference.map((reference) => `- [${reference.label}](${reference.path})`));
  }
  if (node.permissions) {
    body.push(`Permissions: ${node.permissions === "ALL" ? "ALL" : node.permissions.join(", ") || "None"}.`);
  }
  if (node.dependencies.length > 0) {
    body.push(`Depends on: ${node.dependencies.map((item) => `\`${item}\``).join(", ")}.`);
  }
  if (node.children.length > 0) {
    body.push("### Included chapters");
    body.push(...node.children.map((child) => `- \`${child.id}\` ${child.name}`));
  }
  if (node.scenarios.length > 0) {
    body.push("### Acceptance scenarios");
    node.scenarios.forEach((scenario, index) => {
      const scenarioId = scenario.id ?? `SCN-${node.id}-${String(index + 1).padStart(2, "0")}`;
      body.push(`#### ${scenarioId} ${scenario.name}`);
      if (scenario.actor) {
        body.push(`Actor: ${scenario.actor}.`);
      }
      if (scenario.steps.length === 0) {
        body.push("- No steps defined yet.");
        return;
      }
      scenarioStepGroups(scenario, scenarioId).forEach((step) => {
        body.push(`- **Step ${step.id}**`);
        for (const key of ["Given", "When", "Then"] as const) body.push(`  - **${key}:** ${step[key]}`);
        if (step.references.length) body.push(`  - **References:** ${step.references.map((value) => `\`${value}\``).join(", ")}`);
      });
    });
  }

  sections.push({ heading: title, body });
  node.children.forEach((child) => buildMarkdownSections(child, sections, depth + 1));
}

function flattenChapterList(nodes: RequirementNode[], depth: number): string[] {
  return nodes.flatMap((node) => {
    const prefix = `${"  ".repeat(depth)}-`;
    return [`${prefix} \`${node.id}\` ${node.name}`, ...flattenChapterList(node.children, depth + 1)];
  });
}

export function taskTreeToMarkdown(root: RequirementNode): string {
  const sections: MarkdownSection[] = [];
  root.children.forEach((child) => buildMarkdownSections(child, sections, 0));

  const intro = [
    `# ${root.name}`,
    "",
    root.description || "Task description is not available yet.",
    "",
  ];
  if (root.images?.length) {
    intro.push(...root.images.map((image) => `![${[image.id, image.page ?? image.label].filter(Boolean).join(" · ")}](${image.path})`), "");
  }
  if (root.reference?.length) {
    intro.push("## References", "", ...root.reference.map((reference) => `- [${reference.label}](${reference.path})`), "");
  }
  if (root.roles?.length) {
    intro.push("## Roles", "", ...root.roles.map((role) => `- **${role.id}:** ${role.name}`), "");
  }
  if (root.permissions) {
    intro.push(`Permissions: ${root.permissions === "ALL" ? "ALL" : root.permissions.join(", ") || "None"}.`, "");
  }

  const sectionMarkdown = sections
    .map((section) => [section.heading, "", ...section.body, ""].join("\n"))
    .join("\n");

  return [...intro, sectionMarkdown.trim()].join("\n").trim();
}

export function findNodeById(root: RequirementNode, nodeId: string): RequirementNode | null {
  if (root.id === nodeId) {
    return root;
  }
  for (const child of root.children) {
    const match = findNodeById(child, nodeId);
    if (match) {
      return match;
    }
  }
  return null;
}

export function cloneRequirementTree(node: RequirementNode): RequirementNode {
  return {
    ...node,
    dependencies: [...node.dependencies],
    children: node.children.map(cloneRequirementTree),
    scenarios: node.scenarios.map((scenario) => ({
      ...scenario,
      steps: scenario.steps.map((step) => ({ ...step, references: step.references ? [...step.references] : undefined })),
    })),
  };
}

export function updateNodeInTree(
  root: RequirementNode,
  nodeId: string,
  updater: (node: RequirementNode) => RequirementNode,
): RequirementNode {
  if (root.id === nodeId) {
    return updater(root);
  }
  return {
    ...root,
    children: root.children.map((child) => updateNodeInTree(child, nodeId, updater)),
  };
}

export function removeNodeFromTree(root: RequirementNode, nodeId: string): RequirementNode {
  return {
    ...root,
    children: root.children
      .filter((child) => child.id !== nodeId)
      .map((child) => removeNodeFromTree(child, nodeId)),
  };
}

export function appendChildNode(root: RequirementNode, parentId: string, child: RequirementNode): RequirementNode {
  return updateNodeInTree(root, parentId, (node) => ({
    ...node,
    type: "FOLDER",
    children: [...node.children, child],
  }));
}

export function appendSiblingNode(root: RequirementNode, nodeId: string, sibling: RequirementNode): RequirementNode {
  if (root.children.some((child) => child.id === nodeId)) {
    return {
      ...root,
      type: "FOLDER",
      children: [...root.children, sibling],
    };
  }

  return {
    ...root,
    children: root.children.map((child) => appendSiblingNode(child, nodeId, sibling)),
  };
}

export function findParentNode(root: RequirementNode, nodeId: string): RequirementNode | null {
  if (root.children.some((child) => child.id === nodeId)) {
    return root;
  }
  for (const child of root.children) {
    const match = findParentNode(child, nodeId);
    if (match) {
      return match;
    }
  }
  return null;
}

function nextChildIndex(parent: RequirementNode): number {
  const prefix = parent.id === "ROOT" ? "REQ-" : `${parent.id}.`;
  let maxIndex = 0;

  parent.children.forEach((child) => {
    if (!child.id.startsWith(prefix)) {
      return;
    }

    const suffix = child.id.slice(prefix.length);
    if (!/^\d+$/.test(suffix)) {
      return;
    }

    maxIndex = Math.max(maxIndex, Number(suffix));
  });

  return maxIndex + 1;
}

export function nextChildId(parent: RequirementNode): string {
  const nextIndex = nextChildIndex(parent);
  if (parent.id === "ROOT") {
    return `REQ-${nextIndex}`;
  }
  return `${parent.id}.${nextIndex}`;
}

export function buildNewChildNode(parent: RequirementNode): RequirementNode {
  const id = nextChildId(parent);
  return {
    id,
    name: `New chapter ${parent.children.length + 1}`,
    type: "ATOMIC",
    description: "",
    dependencies: [],
    children: [],
    scenarios: [{ name: "New scenario", steps: [] }],
  };
}

export type ReindexedTaskTreeResult = {
  tree: RequirementNode;
  idMap: Record<string, string>;
};

type DescriptionOutlineNode = {
  name: string;
  description: string;
  children: DescriptionOutlineNode[];
};

function trimBlankLines(lines: string[]): string[] {
  const next = [...lines];
  while (next.length > 0 && !next[0].trim()) {
    next.shift();
  }
  while (next.length > 0 && !next[next.length - 1].trim()) {
    next.pop();
  }
  return next;
}

function parseDescriptionOutline(description: string): {
  rootDescription: string;
  children: DescriptionOutlineNode[];
} | null {
  const normalized = description.replace(/\r\n/g, "\n");
  const lines = normalized.split("\n");
  const headingMatches = lines
    .map((line) => /^(#{1,6})\s+(.+?)\s*$/.exec(line))
    .filter((match): match is RegExpExecArray => Boolean(match));

  if (headingMatches.length === 0) {
    return null;
  }

  const highestLevel = headingMatches.reduce((min, match) => Math.min(min, match[1].length), 6);
  const rootLines: string[] = [];
  const rootChildren: DescriptionOutlineNode[] = [];
  const stack: Array<{ level: number; node: DescriptionOutlineNode }> = [];

  for (const line of lines) {
    const headingMatch = /^(#{1,6})\s+(.+?)\s*$/.exec(line);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const name = headingMatch[2].trim();
      while (stack.length > 0 && stack[stack.length - 1].level >= level) {
        stack.pop();
      }
      const nextNode: DescriptionOutlineNode = {
        name: name || "Untitled section",
        description: "",
        children: [],
      };
      if (stack.length === 0 || level === highestLevel) {
        rootChildren.push(nextNode);
      } else {
        stack[stack.length - 1].node.children.push(nextNode);
      }
      stack.push({ level, node: nextNode });
      continue;
    }

    if (stack.length === 0) {
      rootLines.push(line);
      continue;
    }

    const currentNode = stack[stack.length - 1].node;
    currentNode.description = currentNode.description
      ? `${currentNode.description}\n${line}`
      : line;
  }

  const normalizeOutlineNode = (node: DescriptionOutlineNode): DescriptionOutlineNode => ({
    ...node,
    description: trimBlankLines(node.description.split("\n")).join("\n"),
    children: node.children.map(normalizeOutlineNode),
  });

  return {
    rootDescription: trimBlankLines(rootLines).join("\n"),
    children: rootChildren.map(normalizeOutlineNode),
  };
}

function buildOutlineNodes(parentId: string, outlineChildren: DescriptionOutlineNode[]): RequirementNode[] {
  return outlineChildren.map((outlineNode, index) => {
    const nextId = parentId === "ROOT" ? `REQ-${index + 1}` : `${parentId}.${index + 1}`;
    const children = buildOutlineNodes(nextId, outlineNode.children);
    return {
      id: nextId,
      name: outlineNode.name,
      type: children.length > 0 ? "FOLDER" : "ATOMIC",
      description: outlineNode.description,
      dependencies: [],
      children,
      scenarios: [],
    };
  });
}

export function autoStructureNodeFromDescription(node: RequirementNode, nextDescription: string): RequirementNode {
  const outline = parseDescriptionOutline(nextDescription);
  if (!outline) {
    return {
      ...node,
      description: nextDescription,
    };
  }

  const nextChildren = buildOutlineNodes(node.id, outline.children);
  return {
    ...node,
    description: outline.rootDescription,
    children: nextChildren,
    type: nextChildren.length > 0 ? "FOLDER" : node.type,
  };
}

function cloneWithReindexedIds(
  node: RequirementNode,
  nextId: string,
  idMap: Record<string, string>,
): RequirementNode {
  idMap[node.id] = nextId;
  return {
    ...node,
    id: nextId,
    children: node.children.map((child, index) =>
      cloneWithReindexedIds(
        child,
        nextId === "ROOT" ? `REQ-${index + 1}` : `${nextId}.${index + 1}`,
        idMap,
      )),
  };
}

function remapDependencies(node: RequirementNode, idMap: Record<string, string>): RequirementNode {
  return {
    ...node,
    dependencies: node.dependencies.map((dependency) => idMap[dependency] ?? dependency),
    scenarios: node.scenarios.map((scenario) => ({ ...scenario, steps: scenario.steps.map((step) => ({ ...step, references: step.references?.map((reference) => reference.replace(/\\ref\[requirement\]\(([^)]+)\)/g, (_, id: string) => `\\ref[requirement](${idMap[id] ?? id})`)) })) })),
    children: node.children.map((child) => remapDependencies(child, idMap)),
  };
}

export function reindexRequirementTree(root: RequirementNode): ReindexedTaskTreeResult {
  const idMap: Record<string, string> = {};
  const reindexedTree = cloneWithReindexedIds(root, "ROOT", idMap);
  return {
    tree: remapDependencies(reindexedTree, idMap),
    idMap,
  };
}

export function summarizeTaskTree(root: RequirementNode) {
  let nodeCount = 0;
  let atomicCount = 0;
  const visit = (node: RequirementNode) => {
    nodeCount += 1;
    if (node.type === "ATOMIC") {
      atomicCount += 1;
    }
    node.children.forEach(visit);
  };
  visit(root);
  return { nodeCount, atomicCount };
}
