type TestCase = { name: string; code: string };

export function extractTestCases(content: string): TestCase[] {
  const cases: TestCase[] = [];
  const matcher = /\b(?:test|it)(?:\.(?:only|skip))?\s*\(\s*(["'`])([^"'`]+)\1/g;
  let match: RegExpExecArray | null;

  while ((match = matcher.exec(content))) {
    let index = content.indexOf("(", match.index);
    let depth = 0;
    let quote = "";
    let escaped = false;
    let comment = "";
    for (; index < content.length; index += 1) {
      const character = content[index];
      const next = content[index + 1];
      if (comment === "line") {
        if (character === "\n") comment = "";
        continue;
      }
      if (comment === "block") {
        if (character === "*" && next === "/") { comment = ""; index += 1; }
        continue;
      }
      if (quote) {
        if (escaped) escaped = false;
        else if (character === "\\") escaped = true;
        else if (character === quote) quote = "";
        continue;
      }
      if (character === "/" && next === "/") {
        comment = "line";
        index += 1;
      } else if (character === "/" && next === "*") {
        comment = "block";
        index += 1;
      } else if (character === "\"" || character === "'" || character === "`") {
        quote = character;
      } else if (character === "(") {
        depth += 1;
      } else if (character === ")") {
        depth -= 1;
        if (depth === 0) {
          index += 1;
          if (content[index] === ";") index += 1;
          break;
        }
      }
    }
    cases.push({ name: match[2], code: content.slice(match.index, index).trim() });
    matcher.lastIndex = index;
  }
  return cases;
}

