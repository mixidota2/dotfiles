import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";

const STATE_ENTRY_TYPE = "autonomous-loop-state";
const DEFAULT_LOOP_MINUTES = 60;
const DEFAULT_MAX_ROUNDS = 5;

type LoopState = {
	unattended: boolean;
	loopActive: boolean;
	startedAtMs: number;
	budgetMs: number;
	round: number;
	maxRounds: number;
	focus: string;
	wrapUpInjected: boolean;
	lastAssistantText: string;
};

const UNATTENDED_PROMPT_RE =
	/(?:席外す|席を外す|外出てる|外出中|風呂入って|飯食って|ご飯行って|寝るから|帰るから|確認(?:系)?は(?:全部|まとめて)?あとで|承認(?:とか)?できないから)/i;

const AUTO_LOOP_PROMPT_RE =
	/(?:(\d+)\s*(h|hr|hrs|hour|hours|時間|m|min|mins|分)[^\n。]*(?:作業して|繰り返して|回して|改善して|ブラッシュアップ)|(?:何回か|ずっと|いい感じになるまで)[^\n。]*(?:レビュー|改善|改修)[^\n。]*(?:繰り返して|回して|揉んで))/i;

const AutonomyParams = Type.Object({
	mode: Type.Union([
		Type.Literal("unattended"),
		Type.Literal("loop"),
		Type.Literal("converged"),
		Type.Literal("normal"),
	]),
	minutes: Type.Optional(
		Type.Number({
			description: "Time budget in minutes when mode='loop' (default: 60, clamped to 10..240).",
		}),
	),
	max_rounds: Type.Optional(
		Type.Number({
			description: "Maximum review-fix-verify rounds when mode='loop' (default: 5, clamped to 2..8).",
		}),
	),
	focus: Type.Optional(
		Type.String({
			description: "Review and improvement perspectives when mode='loop' (e.g. 'UX改善・バグ修正・画面文言').",
		}),
	),
});

export type ClockFn = () => number;

export default function autonomousLoopExtension(pi: ExtensionAPI, customNow?: ClockFn): void {
	const now = customNow ?? (() => Date.now());
	let state: LoopState = freshState();

	function syncEnv(): void {
		process.env.PI_UNATTENDED = state.unattended || state.loopActive ? "1" : "0";
	}

	function persistState(): void {
		try {
			pi.appendEntry(STATE_ENTRY_TYPE, { ...state });
		} catch {
			// Ignore persistence failures outside active sessions.
		}
	}

	function updateStatus(ctx?: ExtensionContext): void {
		syncEnv();
		if (!ctx?.hasUI) return;
		if (state.loopActive) {
			const remMin = Math.max(0, Math.ceil((state.startedAtMs + state.budgetMs - now()) / 60_000));
			ctx.ui.setStatus("autonomous-loop", `🔁 loop R${state.round}/${state.maxRounds} · ${remMin}m left`);
			return;
		}
		if (state.unattended) {
			ctx.ui.setStatus("autonomous-loop", "🤖 unattended");
			return;
		}
		ctx.ui.setStatus("autonomous-loop", undefined);
	}

	function stopAll(ctx?: ExtensionContext): void {
		if (!state.unattended && !state.loopActive) {
			syncEnv();
			return;
		}
		state = freshState();
		persistState();
		updateStatus(ctx);
	}

	pi.registerTool({
		name: "autonomy_control",
		label: "Autonomy Control",
		description:
			"Agent-managed switch for unattended execution ('unattended') or time-bounded multi-round review-fix-verify loops ('loop') without requiring the user to type slash commands.",
		promptSnippet:
			"Self-manage unattended mode or multi-round review-fix-verify loops when the user asks you to work while they are away",
		promptGuidelines: [
			"Call autonomy_control with mode='unattended' when the user indicates they will be away, busy, or wants tasks finished end-to-end without stopping for questions (e.g. '飯食ってくる', '確認はあとでまとめてやる', '残りの子タスクを片付けて').",
			"Call autonomy_control with mode='loop' (setting minutes and focus) when the user asks for repeated/multi-round review and improvement or specifies a duration to keep improving (e.g. '1時間外出るからレビューと改善回しといて', 'サブエージェントと何回か揉んで改善しておいて').",
			"Call autonomy_control with mode='converged' during an active loop once independent reviewers find 0 P0/P1 issues and all checks pass. Never call autonomy_control for ordinary single-turn requests, proposals, or normal interactive chats.",
		],
		parameters: AutonomyParams,
		async execute(_toolCallId, params, _signal, _onUpdate, ctx) {
			if (params.mode === "normal" || params.mode === "converged") {
				const finishedLoop = state.loopActive && params.mode === "converged";
				stopAll(ctx);
				return {
					content: [
						{
							type: "text",
							text: finishedLoop
								? "Loop marked converged and deactivated. Report the final verified summary to the user."
								: "Returned to normal interactive mode.",
						},
					],
					details: { mode: params.mode, active: false },
				};
			}

			if (params.mode === "unattended") {
				state.unattended = true;
				persistState();
				updateStatus(ctx);
				return {
					content: [
						{
							type: "text",
							text: buildInitialAutonomousContract(state, now()),
						},
					],
					details: { mode: "unattended", active: true },
				};
			}

			const minutes = clampInt(params.minutes, 10, 240, DEFAULT_LOOP_MINUTES);
			const maxRounds = clampInt(params.max_rounds, 2, 8, DEFAULT_MAX_ROUNDS);
			const focus = (params.focus ?? "").trim() || "UX改善・バグ修正・設計整合性";
			state = {
				unattended: true,
				loopActive: true,
				startedAtMs: now(),
				budgetMs: minutes * 60_000,
				round: 1,
				maxRounds,
				focus,
				wrapUpInjected: false,
				lastAssistantText: "",
			};
			persistState();
			updateStatus(ctx);
			return {
				content: [
					{
						type: "text",
						text: `${buildInitialAutonomousContract(state, now())}\n\n${buildLoopRoundChecklist(state)}`,
					},
				],
				details: { mode: "loop", minutes, maxRounds, focus, active: true },
			};
		},
	});

	pi.on("session_start", async (_event, ctx) => {
		restoreStateFromBranch(ctx, (restored) => {
			state = restored;
		});
		updateStatus(ctx);
	});

	// Whenever the human sends a fresh interactive message, reset any previous unattended/loop state
	// so everyday conversation never stays stuck in an old mode without typing '/off'.
	pi.on("input", async (event, ctx) => {
		if (event.source !== "extension" && event.streamingBehavior === undefined) {
			stopAll(ctx);
		}
	});

	// Pressing Escape immediately cancels any active unattended/loop state.
	pi.on("agent_settled", async (event, ctx) => {
		if ((event as { aborted?: boolean }).aborted) {
			stopAll(ctx);
		}
	});

	pi.on("before_agent_start", async (event, ctx) => {
		if (event.messageType !== "user") return;
		const prompt = String(event.prompt ?? "").trim();
		if (!prompt) return;

		const core = prompt.split(/\n\[(?:TK HANDOFF|TK WRAP-UP|EVIDENCE|DESIGN PLAN|PROPOSAL-ONLY)/)[0]?.trim() ?? prompt;
		if (UNATTENDED_PROMPT_RE.test(core)) {
			state.unattended = true;
		}
		const parsedLoop = parseLoopTriggerFromPrompt(core);
		if (parsedLoop && !state.loopActive) {
			state.unattended = true;
			state.loopActive = true;
			state.startedAtMs = now();
			state.budgetMs = parsedLoop.minutes * 60_000;
			state.round = 1;
			state.maxRounds = DEFAULT_MAX_ROUNDS;
			state.focus = parsedLoop.focus;
			state.wrapUpInjected = false;
			persistState();
		}
		updateStatus(ctx);

		if (!state.loopActive && !state.unattended) return;
		return {
			message: {
				customType: "autonomous-loop-contract",
				content: buildInitialAutonomousContract(state, now()),
				display: false,
			},
		};
	});

	pi.on("message_end", async (event) => {
		const msg = (event as { message?: { role?: string; content?: unknown } }).message;
		if (msg?.role === "assistant") {
			const text = extractTextContent(msg.content);
			if (text) state.lastAssistantText = text;
		}
	});

	pi.on("tool_call", async (event) => {
		if (!state.unattended && !state.loopActive) return;
		if (event.toolName === "ask_user_question") {
			return {
				block: true,
				reason: "Unattended mode is active: the operator is away and cannot answer questionnaires right now. Choose the safest reversible option aligned with AGENTS.md and existing tk records, log any open assumption to tk as a hypothesis/question, and continue autonomous execution.",
			};
		}
	});

	pi.on("agent_before_settle", async (event, ctx) => {
		updateStatus(ctx);
		if (!state.loopActive || event.outcome !== "completed" || !event.context.canContinue) return;

		// Do not advance loop rounds while background async subagents are waiting to wake this session.
		if (isWaitingForAsyncSubagentText(state.lastAssistantText)) {
			return;
		}

		if (state.lastAssistantText.includes("[LOOP_CONVERGED]")) {
			stopAll(ctx);
			return;
		}

		const elapsedMs = now() - state.startedAtMs;
		const remainingMs = state.budgetMs - elapsedMs;
		const reserveMs = computeWrapUpReserveMs(state.budgetMs);

		if (remainingMs > reserveMs && state.round < state.maxRounds && !state.wrapUpInjected) {
			state.round += 1;
			persistState();
			updateStatus(ctx);
			return {
				entries: [
					{
						type: "custom_message" as const,
						customType: "autonomous-loop",
						content: buildNextRoundPrompt(state, Math.ceil(remainingMs / 60_000)),
						display: false,
					},
				],
				continue: true,
			};
		}

		if (!state.wrapUpInjected) {
			state.wrapUpInjected = true;
			persistState();
			updateStatus(ctx);
			return {
				entries: [
					{
						type: "custom_message" as const,
						customType: "autonomous-loop",
						content: buildWrapUpPrompt(state),
						display: false,
					},
				],
				continue: true,
			};
		}

		stopAll(ctx);
	});
}

function freshState(): LoopState {
	return {
		unattended: false,
		loopActive: false,
		startedAtMs: 0,
		budgetMs: DEFAULT_LOOP_MINUTES * 60_000,
		round: 0,
		maxRounds: DEFAULT_MAX_ROUNDS,
		focus: "UX改善・バグ修正・設計整合性",
		wrapUpInjected: false,
		lastAssistantText: "",
	};
}

function clampInt(val: number | undefined, min: number, max: number, fallback: number): number {
	if (typeof val !== "number" || !Number.isFinite(val)) return fallback;
	return Math.max(min, Math.min(max, Math.round(val)));
}

function restoreStateFromBranch(ctx: ExtensionContext, apply: (s: LoopState) => void): void {
	try {
		const branch = ctx.sessionManager?.getBranch?.() ?? [];
		for (let i = branch.length - 1; i >= 0; i -= 1) {
			const entry = branch[i] as { type?: string; customType?: string; data?: Partial<LoopState> } | undefined;
			if (entry?.type === "custom" && entry.customType === STATE_ENTRY_TYPE && entry.data) {
				apply({
					...freshState(),
					...entry.data,
					unattended: Boolean(entry.data.unattended),
					loopActive: Boolean(entry.data.loopActive),
				});
				return;
			}
		}
	} catch {
		// Ignore branch read errors.
	}
}

function computeWrapUpReserveMs(budgetMs: number): number {
	return Math.min(10 * 60_000, Math.max(4 * 60_000, Math.floor(budgetMs * 0.18)));
}

function parseLoopTriggerFromPrompt(prompt: string): { minutes: number; focus: string } | null {
	const m = prompt.match(AUTO_LOOP_PROMPT_RE);
	if (!m) return null;
	let minutes = DEFAULT_LOOP_MINUTES;
	if (m[1]) {
		const val = Number.parseInt(m[1], 10);
		const unit = (m[2] ?? "m").toLowerCase();
		minutes = unit.startsWith("h") || unit === "時間" ? val * 60 : val;
	}
	minutes = Math.max(15, Math.min(240, minutes));
	return { minutes, focus: prompt.slice(0, 140).replace(/\s+/g, " ") };
}

function isWaitingForAsyncSubagentText(text: string): boolean {
	const t = text.trim();
	if (!t || t.length > 700) return false;
	return /(?:完了通知が届き次第|完了を待ちます|完了通知を待って|並列ワークフロー.*を起動しました|レーン.*を実行中です)/.test(t);
}

function buildInitialAutonomousContract(state: LoopState, nowMs: number): string {
	const remMin = state.loopActive ? Math.max(1, Math.ceil((state.startedAtMs + state.budgetMs - nowMs) / 60_000)) : 0;
	const lines = [
		"[AUTONOMOUS EXECUTION CONTRACT]",
		"- 離席・自律稼働モードが有効です。`ask_user_question` で停止せず、既存の `AGENTS.md` と `tk` の決定事項に沿って安全で可逆な選択を取り、未確認の前提は `tk` に `hypothesis` / `question` として残して前進してください。",
		"- `subagent(list)` / `subagent(models)` / `subagent(guide)` で時間を消費せず、直接 `code-reviewer`（`gemini-4-carbon` 既定・`git diff` 対応）および `worker`（`gemini-4-argon` 既定・`worktree: true`）を起動してください。",
		"- デモ稼働中の `main` ブランチを壊さないよう作業は分離 worktree で行い、統合後は不要になった一時 worktree・ブランチを必ず削除してください。",
		"- 稼働中サーバーを再起動する際は、停止前に `ps eww -p <PID>` 等で既存の環境変数（`*_ALLOWED_ORIGINS` 等の CORS 設定）を確認して引き継ぎ、再起動後はフロントエンド Origin からの `OPTIONS` プリフライトと画面応答を確認してください。",
	];
	if (state.loopActive) {
		lines.push(
			`- 反復改善ループ（Round ${state.round}/${state.maxRounds}、残り約 ${remMin} 分、観点：${state.focus}）が有効です。1ラウンドで勝手に終了せず、2系統独立レビューで P0/P1 指摘が 0 件になって収束したときのみ \`autonomy_control({ mode: "converged" })\` を呼んで（または応答末尾に \`[LOOP_CONVERGED]\` を付けて）終了してください。`,
		);
	}
	return lines.join("\n");
}

function buildLoopRoundChecklist(state: LoopState): string {
	return `各ラウンドの進行手順（観点: ${state.focus}）:
1. \`code-reviewer\` サブエージェントを2系統以上の視点で並列起動し、直近コミット差分・UI不変条件・日本語ラベル・5層境界を独立監査する。
2. 根拠のあるバグ・UX阻害・整合性欠陥のみを採用する（頼まれていない機能や「決めること：」等のメタ見出しの水増し、複雑度の悪化は禁止）。
3. 分離 worktree 上で \`worker\` に実装・テスト追加させ、統合後に一時 worktree を削除する。
4. Backend（pytest/ruff/ty）・Frontend（vitest/tsc/biome/build）・\`lens_diagnostics(source="lsp", scope="paths")\` をすべて通過させてコミットする。`;
}

function buildNextRoundPrompt(state: LoopState, remainingMinutes: number): string {
	return `[AUTONOMOUS IMPROVEMENT LOOP — ROUND ${state.round}/${state.maxRounds} (残り約 ${remainingMinutes} 分)]
前ラウンドの作業が完了しましたが、時間バジェットが約 ${remainingMinutes} 分残っています（観点: ${state.focus}）。
続けて第 ${state.round} ラウンドを実施してください：
1. 前ラウンドのコミットを含めた最新状態に対して、\`code-reviewer\` サブエージェントを複数観点で並列起動して独立監査させる。
2. 見つかった P0/P1 指摘（バグ、状態不整合、視点リセット、日本語/UIラベルの不自然さ、複雑度肥大化など）を厳選して修正・検証・コミットする（頼まれていない機能の水増しはしない）。
3. すでに P0/P1 指摘が 0 件で品質が収束していることを確認できた場合は、無理にコードを変えず最終疎通と \`tk\` 記録を確認して \`autonomy_control({ mode: "converged" })\` を呼び、完了報告してください。`;
}

function buildWrapUpPrompt(state: LoopState): string {
	return `[AUTONOMOUS LOOP FINALIZATION (Round ${state.round})]
時間バジェットの終盤に入ったため、新規の機能追加ラウンドは開始せず、以下の最終仕上げを行って完了報告してください：
1. 作業中の差分があれば統合し、不要な一時 git worktree と一時ブランチ（\`git worktree list\`）が残っていないことを確認・削除する。
2. Backend（pytest / ruff / ty）と Frontend（vitest / tsc / biome / build）の両方のチェック、および変更ファイルへの \`lens_diagnostics(source="lsp", scope="paths")\` を実行してエラー 0 件を確認する。
3. ローカル開発サーバーが稼働している場合は、既存の環境変数（CORS の \`*_ALLOWED_ORIGINS\` 等）を維持した状態で最新コードが反映されていることと、フロントエンド Origin からの疎通を確認する。
4. クリーンにコミットし、実施した全ラウンドの成果と残課題を \`tk log\` に記録して報告する。`;
}

function extractTextContent(content: unknown): string {
	if (typeof content === "string") return content;
	if (!Array.isArray(content)) return "";
	return content
		.filter((part): part is { type?: string; text?: string } => Boolean(part) && typeof part === "object")
		.filter((part) => part.type === "text" && typeof part.text === "string")
		.map((part) => part.text ?? "")
		.join("\n")
		.trim();
}

export const __test = {
	parseLoopTriggerFromPrompt,
	isWaitingForAsyncSubagentText,
	computeWrapUpReserveMs,
};
