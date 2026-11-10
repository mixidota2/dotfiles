import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { existsSync, readFileSync } from "node:fs";
import { basename, extname, isAbsolute, resolve } from "node:path";

const MAX_AUTOMATIC_REMINDERS = 2;

type Verification = {
	command: string;
	passed: boolean;
	mutationVersion: number;
};

type GateState = {
	dirty: boolean;
	changedFiles: Set<string>;
	touchedMarkdownFiles: Set<string>;
	mutationVersion: number;
	verification?: Verification;
	waiver?: string;
	remindedVersion?: number;
	docQualityRemindedVersion?: number;
	reminderCount: number;
	userPrompt: string;
	requiresEvidence: boolean;
	proposalOnly: boolean;
	evidenceCount: number;
	lastAssistantText: string;
	askedUserQuestion: boolean;
	evidenceReminded: boolean;
};

export default function verificationGate(pi: ExtensionAPI): void {
	let state = freshState();

	function reset(ctx?: ExtensionContext): void {
		state = freshState();
		updateStatus(ctx);
	}

	function markMutation(label: string, ctx: ExtensionContext): void {
		state.dirty = true;
		state.mutationVersion += 1;
		state.verification = undefined;
		state.waiver = undefined;
		if (label) state.changedFiles.add(label);
		updateStatus(ctx);
	}

	function updateStatus(ctx?: ExtensionContext): void {
		if (!ctx?.hasUI) return;
		if (!state.dirty) {
			ctx.ui.setStatus("verification-gate", undefined);
			return;
		}
		if (state.waiver) {
			ctx.ui.setStatus("verification-gate", "⊘ verification waived");
			return;
		}
		if (isFreshPass(state)) {
			ctx.ui.setStatus("verification-gate", `✓ verified: ${shortCommand(state.verification!.command)}`);
			return;
		}
		if (state.verification && !state.verification.passed) {
			ctx.ui.setStatus("verification-gate", `✗ failed: ${shortCommand(state.verification.command)}`);
			return;
		}
		const count = state.changedFiles.size;
		ctx.ui.setStatus("verification-gate", `⚠ unverified${count ? ` (${count})` : ""}`);
	}

	pi.on("session_start", async (_event, ctx) => updateStatus(ctx));

	pi.on("input", async (event, ctx) => {
		if (event.source !== "extension" && event.streamingBehavior === undefined) {
			reset(ctx);
			const text = typeof (event as { text?: unknown }).text === "string" ? (event as { text: string }).text : "";
			if (text) {
				state.userPrompt = text;
				state.requiresEvidence = isSpecOrReasonQuery(text);
				state.proposalOnly = isProposalOnlyPrompt(text);
			}
		}
	});

	pi.on("before_agent_start", async (event) => {
		if (event.messageType !== "user") return;
		const prompt = String(event.prompt ?? "").trim();
		if (!prompt) return;
		state.userPrompt = prompt;
		state.requiresEvidence = isSpecOrReasonQuery(prompt);
		state.proposalOnly = isProposalOnlyPrompt(prompt);
		if (state.waiver) return;
		const contracts: string[] = [];
		if (state.proposalOnly && !state.askedUserQuestion) {
			contracts.push(buildProposalOnlyPrecheckMessage());
		}
		if (state.requiresEvidence) {
			contracts.push(buildEvidencePrecheckMessage());
		}
		if (contracts.length === 0) return;
		return {
			message: {
				customType: "evidence-gate-contract",
				content: contracts.join("\n\n"),
				display: false,
			},
		};
	});

	pi.on("message_end", async (event) => {
		const msg = (event as { message?: { role?: string; content?: unknown } }).message;
		if (msg?.role === "assistant") {
			const text = extractMessageText(msg.content);
			if (text) state.lastAssistantText = text;
		}
	});

	pi.on("tool_call", async (event, ctx) => {
		if (event.toolName === "ask_user_question" || event.toolName === "plan_mode_question") {
			state.askedUserQuestion = true;
			return;
		}
		if (state.proposalOnly && !state.askedUserQuestion && !state.waiver) {
			if (event.toolName === "edit" || event.toolName === "write") {
				return {
					block: true,
					reason: "Proposal/consultation-only request detected: do not run edit/write before presenting your proposal to the user and obtaining approval (or confirming via ask_user_question).",
				};
			}
			if (event.toolName === "bash") {
				const cmd = String((event.input as { command?: unknown }).command ?? "");
				if (isMutatingCommand(cmd)) {
					return {
						block: true,
						reason: "Proposal/consultation-only request detected: mutating bash commands are blocked until you present your proposal and obtain user approval.",
					};
				}
			}
		}
		if (event.toolName !== "bash") return;
		const command = String((event.input as { command?: unknown }).command ?? "");
		if (isVerificationCommand(command) && ctx.hasUI) {
			ctx.ui.setStatus("verification-gate", `… verifying: ${shortCommand(command)}`);
		}
	});

	pi.on("tool_result", async (event, ctx) => {
		if (event.toolName === "ask_user_question" || event.toolName === "plan_mode_question") {
			state.askedUserQuestion = true;
			return;
		}
		if ((event.toolName === "edit" || event.toolName === "write") && !event.isError) {
			const path = String((event.input as { path?: unknown }).path ?? event.toolName);
			const cwd = ctx?.cwd ?? process.cwd();
			const absPath = isAbsolute(path) ? resolve(path) : resolve(cwd, path);
			if (isTargetMarkdownPath(absPath)) {
				state.touchedMarkdownFiles.add(absPath);
			}
			markMutation(path, ctx);
			return;
		}

		if (event.toolName !== "bash") {
			if (!event.isError && EVIDENCE_TOOLS.has(event.toolName)) {
				state.evidenceCount += 1;
			}
			return;
		}
		const command = String((event.input as { command?: unknown }).command ?? "");
		if (!event.isError && isEvidenceBashCommand(command)) {
			state.evidenceCount += 1;
		}
		if (isVerificationCommand(command)) {
			state.verification = {
				command,
				passed: !event.isError,
				mutationVersion: state.mutationVersion,
			};
			updateStatus(ctx);
			return;
		}

		if (!event.isError && isMutatingCommand(command)) markMutation(`bash: ${shortCommand(command)}`, ctx);
	});

	pi.on("agent_before_settle", async (event, ctx) => {
		updateStatus(ctx);
		if (event.outcome !== "completed" || !event.context.canContinue) return;
		if (state.waiver) return;

		if (state.touchedMarkdownFiles.size > 0 && state.docQualityRemindedVersion !== state.mutationVersion && state.reminderCount < MAX_AUTOMATIC_REMINDERS) {
			const docFindings = collectTouchedMarkdownFindings(state.touchedMarkdownFiles);
			if (docFindings.hard.length > 0) {
				state.docQualityRemindedVersion = state.mutationVersion;
				state.reminderCount += 1;
				if (ctx.hasUI) {
					ctx.ui.setStatus("verification-gate", "… checking document quality");
				}
				return {
					entries: [
						{
							type: "custom_message" as const,
							customType: "verification-gate",
							content: buildMarkdownQualityReminder(docFindings),
							display: false,
						},
					],
					continue: true,
				};
			}
		}

		if (state.dirty && !isFreshPass(state)) {
			if (state.remindedVersion === state.mutationVersion || state.reminderCount >= MAX_AUTOMATIC_REMINDERS) {
				return;
			}

			state.remindedVersion = state.mutationVersion;
			state.reminderCount += 1;
			if (ctx.hasUI) {
				ctx.ui.setStatus("verification-gate", "… auto-verifying changes");
			}
			return {
				entries: [
					{
						type: "custom_message" as const,
						customType: "verification-gate",
						content: buildVerificationReminder(state),
						display: false,
					},
				],
				continue: true,
			};
		}

		if (!state.dirty && state.requiresEvidence && !state.askedUserQuestion && !state.evidenceReminded && state.reminderCount < MAX_AUTOMATIC_REMINDERS) {
			const assistantText = state.lastAssistantText || extractLatestAssistantText(event.context?.contextMessages);
			if (needsEvidenceReminder(state, assistantText)) {
				state.evidenceReminded = true;
				state.reminderCount += 1;
				if (ctx.hasUI) {
					ctx.ui.setStatus("verification-gate", "… verifying evidence");
				}
				return {
					entries: [
						{
							type: "custom_message" as const,
							customType: "verification-gate",
							content: buildEvidenceReminder(state, assistantText),
							display: false,
						},
					],
					continue: true,
				};
			}
		}
	});

	pi.on("agent_end", async (_event, ctx) => {
		updateStatus(ctx);
	});

	pi.registerCommand("verify", {
		description: "Show verification status, explicitly waive it with '/verify waive <reason>', or reset it with '/verify reset'.",
		handler: async (args, ctx) => {
			const value = args.trim();
			if (value === "reset") {
				reset(ctx);
				ctx.ui.notify("Verification state reset.", "info");
				return;
			}
			if (value.startsWith("waive ")) {
				const reason = value.slice("waive ".length).trim();
				if (!reason) {
					ctx.ui.notify("Usage: /verify waive <reason>", "warning");
					return;
				}
				state.waiver = reason;
				updateStatus(ctx);
				ctx.ui.notify(`Verification explicitly waived: ${reason}`, "warning");
				return;
			}
			ctx.ui.notify(statusText(state), isFreshPass(state) ? "info" : "warning");
		},
	});
}

function freshState(): GateState {
	return {
		dirty: false,
		changedFiles: new Set<string>(),
		touchedMarkdownFiles: new Set<string>(),
		mutationVersion: 0,
		reminderCount: 0,
		userPrompt: "",
		requiresEvidence: false,
		proposalOnly: false,
		evidenceCount: 0,
		lastAssistantText: "",
		askedUserQuestion: false,
		evidenceReminded: false,
	};
}

function isFreshPass(state: GateState): boolean {
	return state.verification?.passed === true && state.verification.mutationVersion === state.mutationVersion;
}

function statusText(state: GateState): string {
	if (!state.dirty) return "No file mutations recorded for the current task.";
	if (state.waiver) return `Verification waived: ${state.waiver}`;
	if (isFreshPass(state)) return `Fresh verification passed: ${state.verification!.command}`;
	if (state.verification && !state.verification.passed) return `Latest verification failed: ${state.verification.command}`;
	return `Changes are not verified yet (${state.changedFiles.size || "unknown"} target(s)).`;
}

function buildVerificationReminder(state: GateState): string {
	const changed = [...state.changedFiles].slice(0, 12).map((path) => `- ${path}`).join("\n") || "- changed files/commands were detected";
	const failure = state.verification && !state.verification.passed
		? `\nThe latest verification command failed:\n${state.verification.command}\n`
		: "";
	return `[VERIFICATION GATE]\nChanges were made after the latest successful verification, so the task is not ready to finish.${failure}\nChanged targets:\n${changed}\n\nRun the smallest relevant deterministic check now (test, behavior smoke test, typecheck, lint, build, or equivalent). If it fails, fix the problem and rerun it. Report the exact command and result. Do not claim completion from an import/load check alone when behavior changed.`;
}

function isVerificationCommand(command: string): boolean {
	const normalized = command.replace(/\\\n/g, " ").replace(/\s+/g, " ").trim();
	if (!normalized) return false;
	return VERIFICATION_PATTERNS.some((pattern) => pattern.test(normalized));
}

function isMutatingCommand(command: string): boolean {
	const normalized = command
		.replace(/\\\n/g, " ")
		// Strip benign fd redirects like 2>/dev/null, >/dev/null, 2>&1, and JS arrow functions (=>)
		.replace(/\b\d*>\s*\/dev\/null\b/g, " ")
		.replace(/\b\d+>&\d+\b/g, " ")
		.replace(/=>/g, " ")
		.replace(/\s+/g, " ")
		.trim();
	if (!normalized) return false;
	return MUTATION_PATTERNS.some((pattern) => pattern.test(normalized));
}

function shortCommand(command: string): string {
	const oneLine = command.replace(/\s+/g, " ").trim();
	return oneLine.length <= 48 ? oneLine : `${oneLine.slice(0, 45)}…`;
}

const VERIFICATION_PATTERNS: RegExp[] = [
	/(?:^|[;&|]\s*)(?:uv run\s+(?:--\S+(?:\s+\S+)?\s+)*)?(?:python(?:3)?\s+(?:-[A-Za-z]+\s+)*-m\s+)?(?:pytest|unittest)\b/i,
	/(?:^|[;&|]\s*)(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?(?:test|check|lint|typecheck|build)\b/i,
	/(?:^|[;&|]\s*)npx\s+(?:vitest|jest|tsc|eslint|biome)\b/i,
	/(?:^|[;&|]\s*)(?:vitest|jest|eslint|ruff\s+check|mypy|pyright)\b/i,
	/(?:^|[;&|]\s*)node\s+--test\b/i,
	/(?:^|[;&|]\s*)(?:\/bin\/)?(?:sh|bash|zsh)\s+-n\b/i,
	/(?:^|[;&|]\s*)(?:cargo\s+(?:test|check|clippy)|go\s+(?:test|vet)|mix\s+(?:test|compile|format\s+--check-formatted))\b/i,
	/(?:^|[;&|]\s*)(?:bundle\s+exec\s+rspec|rspec|rake\s+test|mvn\s+(?:test|verify)|gradle\w*\s+(?:test|check))\b/i,
	/(?:^|[;&|]\s*)(?:make\s+(?:test|check|lint)|deno\s+(?:test|check|lint)|swift\s+test|xcodebuild\b[^;&|]*\btest)\b/i,
	/(?:^|[;&|]\s*)(?:elixir\s+[^;&|]*(?:_test\.exs|smoke)|node\s+[^;&|]*(?:test|smoke)[^;&|]*\.(?:js|mjs|cjs|ts))\b/i,
];

const MUTATION_PATTERNS: RegExp[] = [
	/(?:^|[;&|]\s*)(?:apply_patch|sed\s+-i|perl\s+-pi|patch\s+-p\d*|tee\s+\S+|touch\s+|mkdir\s+|cp\s+|mv\s+|rm\s+)/i,
	/(?:^|[;&|]\s*)(?:npm|pnpm|yarn|bun)\s+(?:install|add|remove|uninstall)\b/i,
	/(?:^|[;&|]\s*)(?:uv\s+add|pip\s+install|cargo\s+add|go\s+get|mix\s+deps\.)\b/i,
	/(?:^|[;&|]\s*)git\s+(?:merge|rebase|cherry-pick|apply|restore|checkout|switch)\b/i,
	/(?:^|[^>=])(?:>>|>)\s*[^&|\s]/,
];

export const __test = {
	isVerificationCommand,
	isMutatingCommand,
	isSpecOrReasonQuery,
	isProposalOnlyPrompt,
	isEvidenceBashCommand,
	hasEvidenceOrUnknownCitation,
	needsEvidenceReminder,
	inspectMarkdownQuality,
	isTargetMarkdownPath,
};

const PROHIBITED_DOC_PATTERNS: Array<[RegExp, string]> = [
	[/失うもの/, "「失うもの」という不自然な定型表現は使わず、「トレードオフ」または自然な文（「その代わり〜」）に直してください。"],
	[/使う人/, "「使う人」という相対呼称は使わず、具体的な役割名（売場構成企画部の担当者、事業部の担当者、需要予測Gなど）を主語にしてください。"],
	[/解決の道筋/, "「解決の道筋」という表現は「解決策」に直してください。"],
	[/^#{1,4}\s*(?:作るもの|設計の[0-9０-９一二三四五六七八九十]*つの柱|設計の柱)\s*$/, "「作るもの」「設計の柱」のような曖昧な見出しは避け、具体的な設計対象または標準見出しにしてください。"],
];

const SUBJECTLESS_GOAL_PATTERN = /(?:流れが一通り|一通りローカルで動く|ローカルで動くこと|動くようにする)\s*[。．]?$/;
const CHANGE_SCENARIO_HINT_PATTERN = /(?:変更シナリオ|変えるとき|変えたとき|増やしたとき|追加したとき|直す場所|変更箇所|波及)/;
const CODE_DESIGN_BAD_FILENAME_PATTERN = /(?:^|[\s/│├└─`])(model\.py|table\.py|apply\.py|recommend\.py|operations\.py|utils\.py|common\.py|helpers\.py)(?=$|[\s`#):])/m;

function isTargetMarkdownPath(filePath: string): boolean {
	if (!filePath || extname(filePath).toLowerCase() !== ".md") return false;
	const name = basename(filePath);
	if (name === "SKILL.md" || name === "AGENTS.md" || name === "CLAUDE.md" || name === "APPEND_SYSTEM.md") {
		return false;
	}
	if (filePath.includes("/skills/") || filePath.includes("/skills-local/")) {
		return false;
	}
	return true;
}

function inspectMarkdownQuality(filePath: string, text: string): { hard: string[]; advisory: string[] } {
	const hard: string[] = [];
	const advisory: string[] = [];
	const lines = text.split(/\r?\n/);
	const allLines: Array<{ lineNo: number; text: string }> = lines.map((line, i) => ({ lineNo: i + 1, text: line }));
	const visible: Array<{ lineNo: number; text: string }> = [];
	let inFence = false;
	for (let i = 0; i < lines.length; i += 1) {
		const line = lines[i];
		if (line.trim().startsWith("```")) {
			inFence = !inFence;
			continue;
		}
		if (!inFence) visible.push({ lineNo: i + 1, text: line });
	}

	for (const [pattern, message] of PROHIBITED_DOC_PATTERNS) {
		const matched = visible.filter((item) => pattern.test(item.text)).map((item) => `L${item.lineNo}`);
		if (matched.length > 0) {
			hard.push(`- ${filePath} (${matched.slice(0, 5).join(", ")}): ${message}`);
		}
	}

	const lowerName = basename(filePath).toLowerCase();
	const isDesignDoc = lowerName.includes("design-doc") || lowerName.includes("design_doc") ||
		(text.includes("## Context and Scope") && text.includes("## Goals and Non-goals"));
	if (isDesignDoc) {
		const goalsLines = extractMarkdownSection(visible, /^Goals\b/i);
		for (const item of goalsLines) {
			const trimmed = item.text.trim();
			if (/^(?:[-*]|\d+\.)\s+/.test(trimmed) && SUBJECTLESS_GOAL_PATTERN.test(trimmed)) {
				hard.push(`- ${filePath} (L${item.lineNo}): Goal に主語と達成状態がありません（「誰が・何を見て・何を判断/操作でき・どういう状態になるか」の文に直してください）。`);
			}
		}
		const codeDesignLines = extractMarkdownSection(allLines, /^Code design\b/i);
		if (codeDesignLines.length > 0) {
			const codeDesignText = codeDesignLines.map((item) => item.text).join("\n");
			const badFileMatches = codeDesignLines
				.filter((item) => CODE_DESIGN_BAD_FILENAME_PATTERN.test(item.text))
				.map((item) => `L${item.lineNo}`);
			if (badFileMatches.length > 0) {
				hard.push(`- ${filePath} (${badFileMatches.slice(0, 5).join(", ")}): Code design に多義語・曖昧語・動詞1つの細切れファイル名（model.py, table.py, apply.py, recommend.py, operations.py, utils.py 等）が含まれています。共有データモデルは models/<概念名>.py に集約し、専門モジュールと use_cases/ を分け、リーダブルコードの命名原則に沿った具体的な名前へ直してください。`);
			}
			if (!CHANGE_SCENARIO_HINT_PATTERN.test(codeDesignText)) {
				advisory.push(`- ${filePath} (Code design): 代表的な変更シナリオ（モデル追加、画面変更、予測対象追加、最適化手法追加、データソース変更、保存項目追加、ログ追加など）ごとに変更箇所が局所化されているかの検証を補ってください。`);
			}
		}
	}

	return { hard, advisory };
}

function extractMarkdownSection(
	visible: Array<{ lineNo: number; text: string }>,
	headingPattern: RegExp,
): Array<{ lineNo: number; text: string }> {
	let collecting = false;
	let level = 0;
	const out: Array<{ lineNo: number; text: string }> = [];
	for (const item of visible) {
		const m = item.text.trim().match(/^(#{1,6})\s+(.*)$/);
		if (m) {
			const curLevel = m[1].length;
			const title = m[2].trim();
			if (headingPattern.test(title)) {
				collecting = true;
				level = curLevel;
				continue;
			}
			if (collecting && curLevel <= level) break;
		} else if (collecting) {
			out.push(item);
		}
	}
	return out;
}

function collectTouchedMarkdownFindings(files: Set<string>): { hard: string[]; advisory: string[] } {
	const hard: string[] = [];
	const advisory: string[] = [];
	for (const filePath of files) {
		if (!isTargetMarkdownPath(filePath) || !existsSync(filePath)) continue;
		try {
			const text = readFileSync(filePath, "utf8");
			const res = inspectMarkdownQuality(filePath, text);
			hard.push(...res.hard);
			advisory.push(...res.advisory);
		} catch {
			// Ignore unreadable files.
		}
	}
	return { hard, advisory };
}

function buildMarkdownQualityReminder(findings: { hard: string[]; advisory: string[] }): string {
	const items = [...findings.hard, ...findings.advisory].join("\n");
	return `[DOCUMENT QUALITY GATE]
作成・編集した Markdown 文書に、手戻りの原因になる禁止表現または主語欠落が検出されました：
${items}

ユーザーへ完了報告する前に以下を実施して修正してください：
1. 上記の該当箇所を修正する。
2. 書き出したファイル全文を頭から読み直し、すべての文に「誰が・何に対して・何をする（どうなる）か」の主語と述語があるか確かめる。
3. 元の分析の指標（売上・粗利・客数）や用語（DPT と部門など）の揺れ・節間の前後矛盾、および頼まれていないエンハンス項目の混入がないか確かめる。`;
}

const EVIDENCE_TOOLS = new Set([
	"read",
	"read_symbol",
	"read_enclosing",
	"module_report",
	"project_report",
	"symbol_search",
	"effective_config",
	"lens_diagnostics",
	"web_search",
	"web_fetch",
	"subagent",
	"codemode",
	"get_goal",
	"source_check",
	"fetch_content",
	"get_search_content",
]);

const SPEC_OR_REASON_PATTERNS: RegExp[] = [
	/[?？]\s*$/,
	/(?:使える|使えない|できる|できない|可能か|対応してる|対応している|サポートされ)/i,
	/(?:なぜ|どうして|理由|原因|根拠|仕組み|仕様|どういうこと|本当|ほんと)/i,
	/(?:どうすれば|どうしたら|対策|防ぐには|どのレベル|何を設定)/i,
	/\b(?:why|how\s+does|can\s+i|is\s+it\s+possible|what\s+is\s+the\s+evidence|root\s+cause)\b/i,
];

function isSpecOrReasonQuery(prompt: string): boolean {
	const trimmed = prompt.trim();
	if (trimmed.length < 6) return false;
	return SPEC_OR_REASON_PATTERNS.some((pattern) => pattern.test(trimmed));
}

function isEvidenceBashCommand(command: string): boolean {
	const normalized = command.replace(/\\\n/g, " ").replace(/\s+/g, " ").trim();
	if (!normalized) return false;
	// Pure env print/echo commands do not count as repository or specification evidence.
	if (/^(?:printf|echo|pwd|date|whoami|true|false)\b[^|;&]*$/i.test(normalized)) {
		return false;
	}
	return true;
}

function hasEvidenceOrUnknownCitation(text: string): boolean {
	const trimmed = text.trim();
	if (!trimmed) return false;
	return /\[(?:根拠|未確認)\s*[:：][^\]]+\]/.test(trimmed);
}

function needsEvidenceReminder(
	state: Pick<GateState, "requiresEvidence" | "evidenceCount" | "askedUserQuestion">,
	assistantText: string,
): boolean {
	if (!state.requiresEvidence || state.askedUserQuestion) return false;
	const trimmed = assistantText.trim();
	if (!trimmed) return false;
	if (!hasEvidenceOrUnknownCitation(trimmed)) return true;
	// If the model claims [根拠: ...] without having executed any evidence-gathering tool in this turn,
	// require an actual inspection tool unless it explicitly answered as [未確認: ...].
	if (state.evidenceCount === 0 && !/\[未確認\s*[:：][^\]]+\]/.test(trimmed)) {
		return true;
	}
	return false;
}

const PROPOSAL_ONLY_PATTERNS: RegExp[] = [
	/(?:提案して|提案だけ|案を出して|案を考えて|アイデアを出して)/i,
	/(?:どう思う|どう考える|どうしたらいい|どうすればいい|どういう設計|どういう構成|どんな構成)/i,
	/(?:相談したい|議論しよう|議論したい|検討したい|比較して|まずは教えて|まず教えて|構成を知りたい)/i,
	/\b(?:propose|proposal only|what do you think|how should we design)\b/i,
];

const EXPLICIT_IMPLEMENTATION_PATTERNS: RegExp[] = [
	/(?:実装して|直して|修正して|書き換えて|変更して|反映して|更新して|追加して|消して|削除して|作って|書いて|作成して|設定して|コミットして|進めて|進めよう|済むようにして|やめて欲しい|やめてほしい|やって)/i,
	/\b(?:implement|fix|edit|write|update|apply|create|refactor|commit)\b/i,
	/\[DESIGN PLAN MODE CONTRACT\]/i,
	/Implement these selected Pi harness improvements/i,
];

function isProposalOnlyPrompt(prompt: string): boolean {
	const trimmed = prompt.trim();
	if (!trimmed) return false;
	const userPart = trimmed.split(/\n\[(?:TK HANDOFF|EVIDENCE|DESIGN PLAN)/)[0]?.trim() ?? trimmed;
	if (!PROPOSAL_ONLY_PATTERNS.some((pattern) => pattern.test(userPart))) {
		return false;
	}
	if (EXPLICIT_IMPLEMENTATION_PATTERNS.some((pattern) => pattern.test(userPart))) {
		return false;
	}
	return true;
}

function buildProposalOnlyPrecheckMessage(): string {
	return `[PROPOSAL-ONLY BOUNDARY CONTRACT]
この依頼は提案・設計相談・方針検討を求めるものです。以下を厳守してください：
1. ツールを呼ぶ前にまず1〜2文で「何を調べて提案するか」を短く返答する。
2. ファイル編集（\`edit\` / \`write\`）や状態変更コマンドは実行せず、読み取り調査と提案・選択肢の提示（または \`ask_user_question\` での合意形成）までに留める。`;
}

function buildEvidencePrecheckMessage(): string {
	return `[EVIDENCE CONTRACT]
この質問は仕様・可否・原因・根拠・対策に関するものです。回答前に以下を厳守してください：
1. 1つのファイル（低レイヤのSDKや認証コード等）だけを見て結論を出さず、CLI本体・設定スキーマ・ドキュメント（docs/）・警告メッセージ定義まで横断検索（grep / rg / read / web_search）して裏付けを取ること。
2. 主要な主張ごとに直接確認した一次情報を \`[根拠: <ファイルパス:行番号 | 実行コマンド | 公式URL>]\` の形式で明記すること。
3. ツール出力で直接確認していない境界条件や効果を推論で補完せず、未確認事項は必ず \`[未確認: <未確認の論点と確認方法>]\` と明記すること。`;
}

function buildEvidenceReminder(state: Pick<GateState, "evidenceCount">, assistantText: string): string {
	const missingTools = state.evidenceCount === 0
		? "- このターンでファイル読取・検索・Web確認などの調査ツールが実行されていません（環境変数の表示等は仕様の根拠になりません）。\n"
		: "";
	const missingTags = !hasEvidenceOrUnknownCitation(assistantText)
		? "- 回答内に `[根拠: <ファイルパス:行番号 | 実行コマンド | 公式URL>]` または `[未確認: <未確認の論点と確認方法>]` の明記がありません。\n"
		: "";
	return `[EVIDENCE GATE]
仕様・原因・対策に関する回答で、裏付け調査または根拠の明示が不足しています：
${missingTools}${missingTags}
推測や記憶で結論・原因・対策（存在しない設定値や未検証の因果関係など）を補完せず、今すぐ関連コード・設定・ドキュメント・警告定義を実際にツールで調査し、各主張に \`[根拠: ...]\`（未確認の点は \`[未確認: ...]\`）を付記して回答し直してください。`;
}

function extractMessageText(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.filter((part): part is { type?: string; text?: string } => !!part && typeof part === "object")
		.filter((part) => part.type === "text" && typeof part.text === "string")
		.map((part) => part.text!)
		.join("\n")
		.trim();
}

function extractLatestAssistantText(messages: unknown): string {
	if (!Array.isArray(messages)) return "";
	for (let i = messages.length - 1; i >= 0; i -= 1) {
		const msg = messages[i] as { role?: string; content?: unknown } | undefined;
		if (msg?.role === "assistant") {
			const text = extractMessageText(msg.content);
			if (text) return text;
		}
	}
	return "";
}
