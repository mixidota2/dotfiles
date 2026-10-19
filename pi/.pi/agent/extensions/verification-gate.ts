import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";

const MAX_AUTOMATIC_REMINDERS = 2;

type Verification = {
	command: string;
	passed: boolean;
	mutationVersion: number;
};

type GateState = {
	dirty: boolean;
	changedFiles: Set<string>;
	mutationVersion: number;
	verification?: Verification;
	waiver?: string;
	remindedVersion?: number;
	reminderCount: number;
	userPrompt: string;
	requiresEvidence: boolean;
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
			}
		}
	});

	pi.on("before_agent_start", async (event) => {
		if (event.messageType !== "user") return;
		const prompt = String(event.prompt ?? "").trim();
		if (!prompt) return;
		state.userPrompt = prompt;
		state.requiresEvidence = isSpecOrReasonQuery(prompt);
		if (!state.requiresEvidence || state.waiver) return;
		return {
			message: {
				customType: "evidence-gate-contract",
				content: buildEvidencePrecheckMessage(),
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
		mutationVersion: 0,
		reminderCount: 0,
		userPrompt: "",
		requiresEvidence: false,
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
	isEvidenceBashCommand,
	hasEvidenceOrUnknownCitation,
	needsEvidenceReminder,
};

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
