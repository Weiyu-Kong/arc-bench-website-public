import { NavLink, useLocation } from "react-router-dom";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { renderToString } from "katex";
import "katex/dist/katex.min.css";
import { useI18n } from "../i18n/I18nContext";
import MermaidDiagram from "../components/requirements/MermaidDiagram";
import { agentGuideEn, agentGuideZh } from "../docs/agentGuide";

const sectionBlockClassName =
  "rounded-xl border border-[var(--border)] bg-[var(--bg)] p-5 shadow-[0_8px_24px_rgba(0,0,0,0.06)]";

const tableClassName =
  "w-full border-collapse overflow-hidden text-left text-sm text-[var(--text-dim)] [&_code]:rounded-md [&_code]:bg-[var(--bg-elevated)] [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_code]:text-[var(--text)] [&_td]:border-b [&_td]:border-[var(--td-border)] [&_td]:px-4 [&_td]:py-3 [&_th]:border-b [&_th]:border-[var(--border)] [&_th]:bg-[var(--bg-elevated)] [&_th]:px-4 [&_th]:py-3 [&_th]:text-xs [&_th]:font-semibold [&_th]:uppercase [&_th]:text-[var(--text-muted)] [&_tr:last-child_td]:border-b-0";

const proseClassName =
  "text-sm leading-6 text-[var(--text-dim)] [&_code]:rounded-md [&_code]:bg-[var(--bg-elevated)] [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:font-mono [&_code]:text-xs [&_code]:text-[var(--text)]";

const nodeStateExample = `from arcbench_agent_runtime import AgentRuntime

runtime = AgentRuntime.from_env()

runtime.events.mark_run_started("Agent execution started")

runtime.events.mark_design_done("REQ-1", "Login flow design completed")
runtime.events.mark_implementation_done("REQ-1", "Login page and API are wired")
runtime.events.mark_test_passed("REQ-1", "Smoke test passed")
runtime.events.mark_test_failed("REQ-2", "Retry scenario is still failing")

runtime.events.mark_run_completed("Agent execution finished")`;

const traceabilityExample = `from arcbench_agent_runtime import AgentRuntime

runtime = AgentRuntime.from_env()
runtime.traceability.init_db()

runtime.traceability.upsert_interface(
    interface_id="IF-LOGIN-API",
    req_ids=["REQ-1"],
    type="API",
    content="POST /api/auth/login",
    file_path="backend/src/routes/auth.py",
    first_line="18",
)

runtime.traceability.set_interface_implemented(
    "IF-LOGIN-API",
    True,
    "Login handler is connected",
)

runtime.traceability.upsert_test(
    test_id="TEST-LOGIN-E2E",
    req_id="REQ-1",
    type="E2E",
    file_path="tests/login.spec.ts",
    first_line="7",
    interface_ids=["IF-LOGIN-API"],
)

runtime.traceability.set_test_pass_status("TEST-LOGIN-E2E", True)
runtime.traceability.upsert_node_state("REQ-1", "CONVERGED")`;

const gitExample = `from arcbench_agent_runtime import AgentRuntime

runtime = AgentRuntime.from_env()

runtime.git.ensure_repo()

# write files into runtime.paths.project_dir here

runtime.git.commit("REQ-1: implement login flow")

# optional recovery helpers
runtime.git.rollback_last_commit()
# runtime.git.reset_to_commit("<commit_oid>")
# runtime.git.restore_worktree()
# runtime.git.clean_untracked()`;

function CodeBlock({ source }: { source: string }) {
  return (
    <div className="overflow-hidden rounded-xl border border-[var(--border)] bg-[var(--bg-elevated)]">
      <div className="flex h-10 items-center justify-between border-b border-[var(--border)] px-4">
        <span className="rounded-full bg-[var(--bg)] px-2.5 py-1 text-[11px] font-semibold uppercase text-[var(--text-muted)]">
          Python
        </span>
      </div>
      <pre className="m-0 overflow-auto p-4 font-mono text-xs leading-6 text-[var(--text)]">
        <code>{source}</code>
      </pre>
    </div>
  );
}

function ScoringFormula({ source }: { source: string }) {
  return <div className="docs-formula" data-i18n-skip dangerouslySetInnerHTML={{
    __html: renderToString(source, { displayMode: true, throwOnError: false, trust: false }),
  }} />;
}

function ScoringExplanation({ zh }: { zh: boolean }) {
  return <>
    <h3>{zh ? "分数 = 完成质量 × 开销调整" : "Score = completion quality × cost adjustment"}</h3>
    <p>{zh ? "使用自己的 API Key 运行，网站记录用量与费用，并据此计分。先看通过了多少测试，再看为这些通过的测试花了多少钱；同样的通过率下，开销更低会得到奖励，开销更高会受到扣分。以下为默认参数，具体竞赛以其计分配置为准。" : "Run with your own API key. The website records usage and costs for scoring. It starts with tests passed, then adjusts for the cost of achieving those passes. At the same pass rate, lower cost earns a bonus and higher cost incurs a penalty. The defaults below can be overridden by a competition."}</p>
    <ScoringFormula source={String.raw`p=100\frac{K}{N},\qquad C_{\mathrm{eff}}=\max(C,C_{\min}),\qquad E=c_0K`} />
    <ScoringFormula source={String.raw`r=\min\!\left(r_{\max},\max\!\left(r_{\min},\frac{C_{\mathrm{eff}}}{E}\right)\right)`} />
    <ScoringFormula source={String.raw`S=\begin{cases}0,& K=0\ \text{or}\ N=0,\\p\,r^{-\alpha},& K>0,\ r\leq 1,\\p\,r^{-\beta},& K>0,\ r>1.\end{cases}`} />
    <p>{zh ? "没有测试或没有通过测试时直接计 0 分，不计算上述除法。" : "With no tests or no passes, the score is zero and the divisions above are skipped."}</p>
    <table>
      <thead><tr><th>{zh ? "符号" : "Symbol"}</th><th>{zh ? "含义与默认值" : "Meaning and defaults"}</th></tr></thead>
      <tbody>
        <tr><td><i>N, K, p</i></td><td>{zh ? "N 是测试总数，K 是通过数，p 是百分制通过率。例如通过 80/100 个测试，p = 80。" : "N is the total test count, K is the passed count, and p is the pass rate in percent. Passing 80 of 100 tests gives p = 80."}</td></tr>
        <tr><td><i>C, C</i><sub>eff</sub></td><td>{zh ? "C 是计分开销，单位为人民币；C_eff 是加入开销下限后的有效开销。模型代理按网站统一价格统计，未必等于模型供应商实际扣款。" : "C is the scoring cost in CNY; C_eff applies the cost floor. Model-proxy costs use the site's standard prices and may differ from the provider's actual charge."}</td></tr>
        <tr><td><i>C</i><sub>min</sub></td><td>{zh ? "开销下限，默认 0.1 元，避免零开销或极小开销产生过大的奖励。" : "Cost floor: CNY 0.1 by default, preventing zero or tiny costs from creating excessive bonuses."}</td></tr>
        <tr><td><i>c</i><sub>0</sub>, <i>E</i></td><td>{zh ? "c₀ 是每个通过测试的基准开销，默认 0.4 元；E = 0.4 × 通过数，是本次结果的基准总开销。通过得越多，比较时采用的基准开销也越高。" : "c₀ is the reference cost per passed test, CNY 0.4 by default. E = 0.4 × passed tests is the reference total cost. More passes increase this reference."}</td></tr>
        <tr><td><i>r, r</i><sub>min</sub>, <i>r</i><sub>max</sub></td><td>{zh ? "r 表示有效开销是基准开销的多少倍，默认限制在 0.01 到 100 之间。r = 1 表示刚好达到基准，r < 1 更省，r > 1 更贵。" : "r compares effective cost with the reference, bounded between 0.01 and 100 by default. At 1, costs match; below 1 is cheaper; above 1 is more expensive."}</td></tr>
        <tr><td><i>α, β, S</i></td><td>{zh ? "α = 0.1 控制节省开销的奖励强度，β = 0.2 控制超出基准的惩罚强度；S 是最终得分。" : "α = 0.1 controls the bonus for savings, β = 0.2 controls the penalty for overspending, and S is the final score."}</td></tr>
      </tbody>
    </table>
    <h3>{zh ? "如何直观理解" : "Reading the formula"}</h3>
    <ul>
      <li>{zh ? "刚好花到基准开销时，r = 1，调整系数为 1，分数就等于通过率。没有通过测试时，无论开销多少都是 0 分。" : "At the reference cost, r = 1 and the multiplier is 1, so score equals pass rate. With no tests passed, the score is always zero."}</li>
      <li>{zh ? "例如总计 100 个测试、通过 80 个，基准开销为 32 元。花 32 元得 80 分；花 16 元约得 85.7 分；花 64 元约得 69.6 分。" : "With 80 passes out of 100 tests, the reference is CNY 32. Spending CNY 32 gives 80 points, CNY 16 gives about 85.7, and CNY 64 gives about 69.6."}</li>
      <li>{zh ? "选用幂函数，让开销变化带来平滑的分数变化。奖励指数小于惩罚指数，意味着相对基准减半或翻倍时，节省的奖励较温和，超支的惩罚更强，鼓励在完成任务的同时控制成本。" : "The power function changes the score smoothly as costs change. The smaller bonus exponent gives a gentler reward for halving cost than the penalty for doubling it, encouraging both task completion and cost control."}</li>
      <li>{zh ? "比率上下限限制极端开销的影响：默认调整倍数约在 0.398 到 1.585 之间。得分不是百分比，可以超过 100 分。费用缺失、无效或尚未结算时按正无穷处理，经比率上限截断后，得分约为通过率的 39.81%，而不是直接归零。" : "Ratio bounds limit extreme effects: the default multiplier ranges from about 0.398 to 1.585. Score is not a percentage and can exceed 100. Missing, invalid or unsettled costs count as infinity; the upper ratio bound makes the score about 39.81% of the pass rate, rather than zero."}</li>
    </ul>
    <p>{zh ? "综合计分时，先按竞赛规则选取各任务的运行结果，再汇总通过数、测试总数和所选结果的开销，代入同一公式；不是简单平均各任务分数。演化竞赛还会按其阶段与路线规则选取结果。只要所选结果中有一项费用不可用，汇总费用也按正无穷处理。" : "For the overall score, select task results according to competition rules, sum their passed counts, total tests and costs, then apply the same formula. This is not a simple average of task scores. Evolution competitions also apply their stage and route selection rules. Any unavailable selected cost makes the aggregate cost infinite."}</p>
  </>;
}

function OverviewPage() {
  const { language } = useI18n();
  const zh = language === "zh";
  const flow = zh ? `flowchart TD
    A[选择任务与下载模板] --> B[编写智能体并上传 ZIP]
    B --> C[配置模型并启动运行]
    C --> D[系统准备需求与应用起点]
    D --> E[智能体读取需求并生成应用]
    E --> F[系统启动应用并执行测试]
    F --> G[查看结果、开销与报告]
    E -. 可选上报 .-> H[实时进度、代码与预览]` : `flowchart TD
    A[Choose a task and download a template] --> B[Build your agent and upload a ZIP]
    B --> C[Configure a model and start a run]
    C --> D[Platform prepares requirements and application]
    D --> E[Agent reads requirements and builds the app]
    E --> F[Platform starts the app and runs tests]
    F --> G[Review results, costs and reports]
    E -. Optional updates .-> H[Live progress, code and previews]`;
  const competitionStructure = zh ? `flowchart TB
    C[同一个竞赛：包含任务 1、任务 2 等多个任务]
    C --> A[提交 A：上传的一份智能体]
    C --> B[提交 B：另一份智能体]
    subgraph SA[提交 A 的独立成绩]
      direction TB
      A --> T1[任务 1：可多次运行]
      A --> T2[任务 2：可多次运行]
      T1 --> R1[运行得分：0.70、0.90]
      T2 --> R2[运行得分：0.60、0.80]
      R1 --> M1([任务 1 最高分：0.90])
      R2 --> M2([任务 2 最高分：0.80])
      M1 --> TOTAL{{按竞赛规则综合计分}}
      M2 --> TOTAL
    end
    B --> BT[在提交 B 下独立执行同一组任务]
    BT --> BS{{仅用提交 B 的任务最高分综合计分}}` : `flowchart TB
    C[One competition contains Task 1, Task 2 and more]
    C --> A[Submission A: one uploaded agent]
    C --> B[Submission B: another uploaded agent]
    subgraph SA[Independent results for submission A]
      direction TB
      A --> T1[Task 1: repeatable runs]
      A --> T2[Task 2: repeatable runs]
      T1 --> R1[Run scores: 0.70, 0.90]
      T2 --> R2[Run scores: 0.60, 0.80]
      R1 --> M1([Best for Task 1: 0.90])
      R2 --> M2([Best for Task 2: 0.80])
      M1 --> TOTAL{{Combine using competition scoring rules}}
      M2 --> TOTAL
    end
    B --> BT[Run the same tasks independently under B]
    BT --> BS{{Combine only B's best task scores}}`;
  const submissionExecution = zh ? `sequenceDiagram
    actor U as 用户
    participant S as 提交 A
    participant T as 平台执行器
    participant R as 提交 A 的成绩
    U->>S: 向竞赛上传智能体，创建一次提交
    S->>T: 用该提交执行竞赛中的多个任务
    loop 每个任务（可多次运行）
      T->>T: 运行智能体、启动应用、执行测试
      T->>R: 保存此提交、此任务的本次运行得分
      R->>R: 取此提交内该任务的最高分
    end
    R-->>U: 综合各任务最高分，展示提交 A 的得分
    Note over S,R: 新提交拥有独立成绩，不与提交 A 拼接` : `sequenceDiagram
    actor U as User
    participant S as Submission A
    participant T as Platform runner
    participant R as Results for A
    U->>S: Upload an agent to the competition
    S->>T: Execute multiple competition tasks
    loop Each task, with repeatable runs
      T->>T: Run agent, start app, execute tests
      T->>R: Save this run under its submission and task
      R->>R: Select this submission's best score for the task
    end
    R-->>U: Combine best task scores into A's overall score
    Note over S,R: A new submission has independent results`;
  return <div className="docs-prose">
    <h1>{zh ? "网站运行概览" : "Platform overview"}</h1>
    <p className="docs-lead">{zh ? "上传一个能编写应用的智能体，让 ARC-Bench 为它准备任务、运行应用并检验结果。" : "Upload an agent that builds applications. ARC-Bench prepares the task, runs the application and evaluates the result."}</p>
    <h2>{zh ? "从上传到结果" : "From upload to results"}</h2>
    <MermaidDiagram source={flow} />
    <h2>{zh ? "竞赛、提交、任务与运行" : "Competitions, submissions, tasks and runs"}</h2>
    <p>{zh ? "每个竞赛包含多个任务。你针对一个竞赛提交一份智能体，一次提交可以执行该竞赛的多个任务；同一个任务也可以在该提交下多次运行。运行记录始终属于某一次提交、某一个任务。" : "Each competition contains multiple tasks. Upload an agent as a submission to that competition, then use that submission to execute multiple tasks. Each task can run repeatedly under the same submission. Every run belongs to one submission and one task."}</p>
    <MermaidDiagram source={competitionStructure} />
    <p className="text-xs text-[var(--text-muted)]">{zh ? "图中的得分仅用于说明选择规则；实际综合分按竞赛计分规则计算。" : "Scores illustrate the selection rule only; the overall result follows the competition's scoring rules."}</p>
    <h2>{zh ? "一次提交如何计分" : "How one submission is scored"}</h2>
    <p>{zh ? "先在该提交下，分别取每个任务多次运行中的最高分，再综合这些任务成绩，得到这次提交的得分。" : "For each task, select the highest run score within that submission. Combine those task results to obtain the submission's overall score."}</p>
    <div className="my-5 border-l-2 border-[var(--accent)] bg-[var(--bg-elevated)] px-4 py-3">
      <strong>{zh ? "提交 A 的得分 = 综合计分（A 的任务 1 最高分，A 的任务 2 最高分，…）" : "Score for A = combine(best Task 1 score within A, best Task 2 score within A, …)"}</strong>
      <p>{zh ? "综合分只使用同一次提交下的任务成绩，不会把提交 A 的任务 1 和提交 B 的任务 2 拼在一起计算。新建提交后，各任务的运行与成绩独立记录。" : "An overall score uses task results from a single submission. Task 1 from submission A is never combined with Task 2 from submission B. A new submission records its runs and task results independently."}</p>
    </div>
    <MermaidDiagram source={submissionExecution} fitWidth />
    <ScoringExplanation zh={zh} />
    <h2>{zh ? "你需要准备什么" : "What you provide"}</h2>
    <p>{zh ? "选择任务，下载模板并编写智能体；上传 ZIP，然后选择模型、填写所需连接信息并启动运行。智能体的工作是读取需求，将可运行的应用写到指定目录。" : "Choose a task, download a template and implement your agent. Upload the ZIP, configure its model connection and start a run. Your agent reads requirements and writes a runnable application to the supplied output directory."}</p>
    <h2>{zh ? "平台负责什么" : "What the platform handles"}</h2>
    <p>{zh ? "系统准备独立的运行环境、任务需求和应用起点，安装智能体依赖并启动入口程序。智能体完成后，平台启动应用、执行测试，并整理通过率、模型开销和报告。演化任务会使用对应的前序应用作为起点。" : "The platform prepares an isolated environment, requirements and an application baseline, installs agent dependencies and launches the entrypoint. After the agent finishes, it starts the application, runs tests and collects pass rates, model costs and reports. Evolution tasks can start from a previous application."}</p>
    <h2>{zh ? "运行过程中可以看到什么" : "What you can see during a run"}</h2>
    <p>{zh ? "日志会持续显示。接入可选的 Runtime SDK 后，还能查看需求节点进度、实现与测试的关联、Git 提交和应用预览。智能体上报的状态用于可视化，最终评测结果由平台测试决定。" : "Logs stream as the agent runs. Optional Runtime SDK integration adds requirement progress, implementation and test traceability, Git history and application previews. Reported agent states support visualization; platform tests determine evaluation results."}</p>
    <div className="docs-next"><NavLink to="/docs/agent">{zh ? "下一页：编写与上传智能体 →" : "Next: Build and upload an agent →"}</NavLink></div>
  </div>;
}

function NodeStatePage() {
  return (
    <>
      <section className={sectionBlockClassName}>
        <h2 className="mb-3 text-xl font-semibold text-[var(--text)]">Node State APIs</h2>
        <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
        <table className={tableClassName}>
          <thead>
            <tr>
              <th>Method</th>
              <th>Purpose</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><code>runtime.events.mark_design_done(node_id, message=None)</code></td>
              <td>Mark design phase completed for one requirement node.</td>
            </tr>
            <tr>
              <td><code>runtime.events.mark_implementation_done(node_id, message=None)</code></td>
              <td>Mark implementation phase completed for one requirement node.</td>
            </tr>
            <tr>
              <td><code>runtime.events.mark_test_passed(node_id, message=None)</code></td>
              <td>Mark testing phase passed for one requirement node.</td>
            </tr>
            <tr>
              <td><code>runtime.events.mark_test_failed(node_id, message=None)</code></td>
              <td>Mark testing phase failed for one requirement node.</td>
            </tr>
            <tr>
              <td><code>runtime.events.mark_run_started(message=None)</code></td>
              <td>Mark the whole agent run as started.</td>
            </tr>
            <tr>
              <td><code>runtime.events.mark_run_completed(message=None)</code></td>
              <td>Mark the whole agent run as completed.</td>
            </tr>
            <tr>
              <td><code>runtime.events.mark_run_failed(message=None)</code></td>
              <td>Mark the whole agent run as failed.</td>
            </tr>
            <tr>
              <td><code>runtime.events.mark_run_paused(message=None)</code></td>
              <td>Mark the whole agent run as paused.</td>
            </tr>
            <tr>
              <td><code>runtime.events.mark_run_resumed(message=None)</code></td>
              <td>Mark the whole agent run as resumed.</td>
            </tr>
          </tbody>
        </table>
        </div>
      </section>

      <section className={sectionBlockClassName}>
        <h2 className="mb-3 text-xl font-semibold text-[var(--text)]">Example</h2>
        <CodeBlock source={nodeStateExample} />
      </section>
    </>
  );
}

function TraceabilityPage() {
  return (
    <>
      <section className={sectionBlockClassName}>
        <h2 className="mb-3 text-xl font-semibold text-[var(--text)]">Core APIs</h2>
        <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
        <table className={tableClassName}>
          <thead>
            <tr>
              <th>Method group</th>
              <th>Examples</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>Database lifecycle</td>
              <td><code>init_db(reset=False)</code></td>
            </tr>
            <tr>
              <td>Requirements and scenarios</td>
              <td><code>upsert_requirement</code>, <code>update_requirement_fields</code>, <code>delete_requirement</code>, <code>upsert_scenario</code>, <code>delete_scenario</code></td>
            </tr>
            <tr>
              <td>Interfaces</td>
              <td><code>upsert_interface</code>, <code>update_interface_fields</code>, <code>set_interface_implemented</code>, <code>delete_interface</code></td>
            </tr>
            <tr>
              <td>Tests</td>
              <td><code>upsert_test</code>, <code>update_test_fields</code>, <code>set_test_pass_status</code>, <code>set_test_pass_statuses</code>, <code>reset_test_pass_statuses_for_requirement</code>, <code>delete_test</code></td>
            </tr>
            <tr>
              <td>Node status and links</td>
              <td><code>upsert_node_state</code>, <code>delete_node_state</code>, <code>insert_call_edge</code>, <code>delete_call_edge</code></td>
            </tr>
            <tr>
              <td>Node contracts and cleanup</td>
              <td><code>upsert_node_contract</code>, <code>delete_node_contract</code>, <code>clear_node_design_artifacts</code></td>
            </tr>
          </tbody>
        </table>
        </div>
      </section>

      <section className={sectionBlockClassName}>
        <h2 className="mb-3 text-xl font-semibold text-[var(--text)]">Automatic Refresh</h2>
        <p className={proseClassName}>
          These APIs write the live database, atomically refresh
          <code> .arc/traceability/*.json </code>, and append fixed-format events into
          <code> runner-events.jsonl </code>. The backend watches new event lines, reloads data from the snapshot, and
          the frontend refreshes the affected traceability views automatically.
        </p>
      </section>

      <section className={sectionBlockClassName}>
        <h2 className="mb-3 text-xl font-semibold text-[var(--text)]">Example</h2>
        <CodeBlock source={traceabilityExample} />
      </section>
    </>
  );
}

function GitPage() {
  return (
    <>
      <section className={sectionBlockClassName}>
        <h2 className="mb-3 text-xl font-semibold text-[var(--text)]">Git APIs</h2>
        <div className="overflow-x-auto rounded-xl border border-[var(--border)]">
        <table className={tableClassName}>
          <thead>
            <tr>
              <th>Method</th>
              <th>Purpose</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><code>runtime.git.ensure_repo(create_initial_commit=True)</code></td>
              <td>Initialize the repository and configure runtime git identity.</td>
            </tr>
            <tr>
              <td><code>runtime.git.ensure_arc_gitignore()</code></td>
              <td>Write the managed ArcBench gitignore block for runtime artifacts and build outputs.</td>
            </tr>
            <tr>
              <td><code>runtime.git.configure_identity()</code></td>
              <td>Configure git author and committer identity from the runtime environment.</td>
            </tr>
            <tr>
              <td><code>runtime.git.commit(message)</code></td>
              <td>Create one commit and trigger commit-history and preview refresh.</td>
            </tr>
            <tr>
              <td><code>runtime.git.rollback_last_commit(hard=False)</code></td>
              <td>Rollback the latest commit.</td>
            </tr>
            <tr>
              <td><code>runtime.git.reset_to_commit(commit_oid, hard=True)</code></td>
              <td>Reset the repository to a target commit.</td>
            </tr>
            <tr>
              <td><code>runtime.git.restore_worktree()</code></td>
              <td>Discard working tree changes.</td>
            </tr>
            <tr>
              <td><code>runtime.git.clean_untracked()</code></td>
              <td>Remove untracked files.</td>
            </tr>
            <tr>
              <td><code>runtime.git.status_porcelain()</code>, <code>runtime.git.current_head()</code></td>
              <td>Read repository status and the current HEAD commit id.</td>
            </tr>
          </tbody>
        </table>
        </div>
      </section>

      <section className={sectionBlockClassName}>
        <h2 className="mb-3 text-xl font-semibold text-[var(--text)]">Manual File Sync Case</h2>
        <p className={proseClassName}>
          If you bypass the SDK and change git state or traceability artifacts manually, call
          <code> runtime.events.notify_commit_history_changed(...) </code> or
          <code> runtime.events.notify_traceability_changed(...) </code> after the manual write so ArcBench can refresh
          the right panels without exposing raw event payloads.
        </p>
      </section>

      <section className={sectionBlockClassName}>
        <h2 className="mb-3 text-xl font-semibold text-[var(--text)]">Example</h2>
        <CodeBlock source={gitExample} />
      </section>
    </>
  );
}

export default function DocsPage() {
  const { language } = useI18n();
  const zh = language === "zh";
  const { pathname } = useLocation();
  const current = pathname === "/docs/agent" ? "agent" : pathname === "/docs/visualization" ? "visualization" : "overview";
  const pages = [
    { key: "overview", path: "/docs", label: zh ? "网站运行概览" : "Overview" },
    { key: "agent", path: "/docs/agent", label: zh ? "编写与上传智能体" : "Build an agent" },
    { key: "visualization", path: "/docs/visualization", label: zh ? "可视化 API" : "Visualization API" },
  ];
  return (
    <div className="page docs-page">
      <div className="docs-layout">
        <aside className="docs-sidebar">
          <div className="docs-sidebar-title">Docs</div>
          <nav aria-label={zh ? "文档导航" : "Documentation"}>
            {pages.map(page => <NavLink key={page.key} to={page.path} end className={current === page.key ? "docs-nav active" : "docs-nav"}>{page.label}</NavLink>)}
          </nav>
          {current === "visualization" ? <div className="docs-outline">
            <a href="#runtime-intro">{zh ? "运行机制" : "How it works"}</a>
            <a href="#node-state">{zh ? "节点状态" : "Node state"}</a>
            <a href="#traceability">{zh ? "追溯数据" : "Traceability"}</a>
            <a href="#git">Git</a>
          </div> : null}
        </aside>
        <article className="docs-content" key={current}>
          {current === "overview" ? <OverviewPage /> : null}
          {current === "agent" ? <>
            <header className="docs-prose"><h1>{zh ? "编写与上传智能体" : "Build and upload an agent"}</h1><p className="docs-lead">{zh ? "从一个最小示例开始，了解输入、输出、模型配置与执行流程。" : "Start with a minimal example and learn the input, output, model configuration and execution contract."}</p></header>
            <div className="docs-prose"><ReactMarkdown remarkPlugins={[remarkGfm]}>{zh ? agentGuideZh : agentGuideEn}</ReactMarkdown></div>
            <div className="docs-next"><NavLink to="/docs/visualization">{zh ? "下一页：可视化 API →" : "Next: Visualization API →"}</NavLink></div>
          </> : null}
          {current === "visualization" ? <div className="docs-api">
            <header className="docs-prose" id="runtime-intro"><h1>{zh ? "可视化 API" : "Visualization API"}</h1>
              <p className="docs-lead">{zh ? "使用内置 Runtime SDK，让网站展示智能体的执行进度与代码变化。" : "Use the built-in Runtime SDK to show your agent's progress and code changes."}</p>
              <p>{zh ? "这是智能体进程内的 Python SDK，无需请求网站登录 API。使用 AgentRuntime.from_env() 读取平台注入的路径；写应用文件时使用 runtime.paths.project_dir。" : "This is an in-process Python SDK, with no website authentication requests required. AgentRuntime.from_env() reads injected paths; write application files under runtime.paths.project_dir."}</p>
              <p>{zh ? "SDK 写入 .arc/runner-events.jsonl 和 .arc/traceability/*.json；后端监听变化并通知前端刷新。请调用高级方法，不要手动构造事件格式。下载模板中还提供 JavaScript/TypeScript SDK 使用示例。" : "The SDK writes .arc/runner-events.jsonl and .arc/traceability/*.json. The backend watches changes and refreshes the relevant UI. Use high-level methods rather than constructing event payloads. JavaScript and TypeScript SDK examples are included in their templates."}</p>
            </header>
            <div id="node-state"><NodeStatePage /></div>
            <div id="traceability"><TraceabilityPage /></div>
            <div id="git"><GitPage /></div>
          </div> : null}
        </article>
      </div>
    </div>
  );
}
