import { CodeOutlined, FileTextOutlined } from "@ant-design/icons";
import { useMemo } from "react";

import type { RequirementTestFile } from "../../lib/types";

import { extractTestCases } from "../../lib/testSource";

export default function TestSuiteViewer({ files }: { files: RequirementTestFile[] }) {
  const filesWithCases = useMemo(() => files.map((file) => ({ ...file, cases: extractTestCases(file.content) })), [files]);

  if (files.length === 0) {
    return <div className="test-suite-empty"><FileTextOutlined /><div><strong>No tests found</strong><p>This task does not include test files in its test directory.</p></div></div>;
  }

  return (
    <section className="test-suite-viewer" aria-label="Task tests">
      <div className="test-suite-intro"><CodeOutlined /><span>{files.length} test file{files.length === 1 ? "" : "s"}</span></div>
      {filesWithCases.map((file) => (
        <details key={file.path} className="test-suite-file">
          <summary><FileTextOutlined /><span data-i18n-skip>{file.path}</span><small>{file.cases.length ? `${file.cases.length} case${file.cases.length === 1 ? "" : "s"}` : "source"}</small></summary>
          <div className="test-suite-file-body" data-i18n-skip>
            {file.cases.length ? file.cases.map((testCase, index) => (
              <details key={`${testCase.name}-${index}`} className="test-suite-case">
                <summary data-i18n-skip>{testCase.name}</summary>
                <pre><code>{testCase.code}</code></pre>
              </details>
            )) : <pre><code>{file.content}</code></pre>}
          </div>
        </details>
      ))}
    </section>
  );
}
