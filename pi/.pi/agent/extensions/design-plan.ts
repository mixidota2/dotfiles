import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { isAbsolute, relative, resolve } from "node:path";

const STATE_ENTRY_TYPE = "design-plan-state";

type DesignPlanState = {
	enabled: boolean;
	topic?: string;
	designDocPaths: string[];
};

const ALLOWED_DOC_PATH_PATTERNS: RegExp[] = [
	/(?:^|\/)docs\/.+\.md$/i,
	/(?:^|\/)memo\/.+\.md$/i,
	/(?:^|\/)plans?\/.+\.md$/i,
	/(?:^|\/)[^/]*design[_-]?doc[^/]*\.md$/i,
	/(?:^|\/)[^/]*plan[^/]*\.md$/i,
];

const BLOCKED_BASH_PATTERNS: Array<[RegExp, string]> = [
	[/(?:^|[;&|]\s*)(?:apply_patch|sed\s+-i|perl\s+-pi|patch\s+-p\d*|tee\s+\S+|rm\s+|mv\s+|cp\s+)/i, "File-mutating shell command is blocked during design-plan mode."],
	[/(?:^|[;&|]\s*)git\s+(?:commit|push|merge|rebase|cherry-pick|reset|checkout|switch|restore)\b/i, "Mutating git command is blocked during design-plan mode."],
	[/(?:^|[;&|]\s*)(?:npm|pnpm|yarn|bun)\s+(?:install|add|remove|uninstall)\b/i, "Dependency mutation is blocked during design-plan mode."],
	[/(?:^|[;&|]\s*)(?:uv\s+add|uv\s+remove|pip\s+install)\b/i, "Dependency mutation is blocked during design-plan mode."],
];

export default function designPlanExtension(pi: ExtensionAPI): void {
	let state: DesignPlanState = freshState();

	function persistState(): void {
		try {
			pi.appendEntry(STATE_ENTRY_TYPE, {
				enabled: state.enabled,
				topic: state.topic,
				designDocPaths: [...state.designDocPaths],
			});
		} catch {
			// Ignore persistence errors in non-session contexts.
		}
	}

	function updateStatus(ctx?: ExtensionContext): void {
		if (!ctx?.hasUI) return;
		if (!state.enabled) {
			ctx.ui.setStatus("design-plan", undefined);
			return;
		}
		const docLabel = state.designDocPaths.length > 0
			? ` (${state.designDocPaths[state.designDocPaths.length - 1]})`
			: "";
		ctx.ui.setStatus("design-plan", `📐 design-plan: Design Doc only${docLabel}`);
	}

	function restoreFromBranch(ctx: ExtensionContext): void {
		try {
			const branch = ctx.sessionManager?.getBranch?.() ?? [];
			for (let i = branch.length - 1; i >= 0; i -= 1) {
				const entry = branch[i] as { type?: string; customType?: string; data?: Partial<DesignPlanState> } | undefined;
				if (entry?.type === "custom" && entry.customType === STATE_ENTRY_TYPE && entry.data) {
					state = {
						enabled: Boolean(entry.data.enabled),
						topic: typeof entry.data.topic === "string" ? entry.data.topic : undefined,
						designDocPaths: Array.isArray(entry.data.designDocPaths) ? [...entry.data.designDocPaths] : [],
					};
					return;
				}
			}
		} catch {
			// Ignore branch read errors.
		}
	}

	pi.on("session_start", async (_event, ctx) => {
		restoreFromBranch(ctx);
		updateStatus(ctx);
	});

	pi.on("before_agent_start", async (event) => {
		if (!state.enabled || event.messageType !== "user") return;
		return {
			message: {
				customType: "design-plan-contract",
				content: buildDesignPlanContract(state),
				display: false,
			},
		};
	});

	pi.on("tool_call", async (event, ctx) => {
		if (!state.enabled) return;

		if (event.toolName === "edit" || event.toolName === "write") {
			const rawPath = String((event.input as { path?: unknown })?.path ?? "").trim();
			const cwd = ctx?.cwd ?? process.cwd();
			if (!isAllowedDesignDocPath(rawPath, cwd)) {
				return {
					block: true,
					reason: `Design-Plan mode is active: editing implementation/code file '${rawPath || "(unknown)"}' is blocked. Only Design Doc markdown files (e.g., docs/**/design_doc.md, memo/**/*.md, plans/**/*.md) may be written until '/design-plan approve' is run.`,
				};
			}
			const relPath = normalizeRelativePath(rawPath, cwd);
			if (relPath && !state.designDocPaths.includes(relPath)) {
				state.designDocPaths.push(relPath);
				persistState();
				updateStatus(ctx);
			}
			return;
		}

		if (event.toolName === "bash") {
			const command = String((event.input as { command?: unknown })?.command ?? "");
			const blockedReason = findBlockedDesignPlanBashReason(command);
			if (blockedReason) {
				return {
					block: true,
					reason: `${blockedReason} Use read-only inspection commands while Design-Plan mode is active, or run '/design-plan approve' to start implementation.`,
				};
			}
		}
	});

	async function handleDesignPlanCommand(args: string, ctx: any): Promise<void> {
		const trimmed = args.trim();

		if (trimmed === "off" || trimmed === "disable" || trimmed === "exit") {
			state.enabled = false;
			persistState();
			updateStatus(ctx);
			ctx.ui.notify("Design-Plan mode disabled. Code editing unlocked.", "info");
			return;
		}

		if (trimmed === "status") {
			const docs = state.designDocPaths.join(", ") || "none yet";
			ctx.ui.notify(
				state.enabled
					? `Design-Plan mode is ACTIVE (topic: ${state.topic ?? "unspecified"}, docs: ${docs})`
					: `Design-Plan mode is INACTIVE (last docs: ${docs})`,
				"info",
			);
			return;
		}

		if (trimmed === "approve" || trimmed.startsWith("approve ")) {
			const customPath = trimmed.slice("approve".length).trim();
			if (customPath && !state.designDocPaths.includes(customPath)) {
				state.designDocPaths.push(customPath);
			}
			const targetDocs = state.designDocPaths.length > 0
				? state.designDocPaths.join(", ")
				: "承認済みの Design Doc";
			state.enabled = false;
			persistState();
			updateStatus(ctx);
			pi.sendUserMessage(buildApproveImplementationPrompt(targetDocs), { deliverAs: "followUp" });
			ctx.ui.notify(`Design-Plan approved (${targetDocs}). Unlocked code editing and queued implementation.`, "info");
			return;
		}

		if (trimmed === "review" || trimmed.startsWith("review ")) {
			const customPath = trimmed.slice("review".length).trim();
			const targetDocs = customPath || state.designDocPaths.join(", ") || "直近作成した Design Doc";
			pi.sendUserMessage(buildSubagentReviewPrompt(targetDocs), { deliverAs: "followUp" });
			ctx.ui.notify(`Queued Design Doc review for ${targetDocs}.`, "info");
			return;
		}

		if (!trimmed && state.enabled && ctx.hasUI) {
			const choice = await ctx.ui.select("Design-Plan モード操作を選択", [
				"Approve & start implementation (コード編集を解禁して実装開始)",
				"Review Design Doc with subagent & git diff (独立レビュー＋差分確認)",
				"Disable Design-Plan mode (モード終了のみ)",
				"Stay in Design-Plan mode (継続)",
			]);
			if (!choice || choice.startsWith("Stay")) return;
			if (choice.startsWith("Approve")) {
				await handleDesignPlanCommand("approve", ctx);
				return;
			}
			if (choice.startsWith("Review")) {
				await handleDesignPlanCommand("review", ctx);
				return;
			}
			if (choice.startsWith("Disable")) {
				await handleDesignPlanCommand("off", ctx);
				return;
			}
		}

		state.enabled = true;
		if (trimmed && trimmed !== "on") {
			state.topic = trimmed;
		}
		persistState();
		updateStatus(ctx);

		if (trimmed && trimmed !== "on") {
			pi.sendUserMessage(buildKickoffPrompt(trimmed), { deliverAs: "followUp" });
			ctx.ui.notify(`Design-Plan mode enabled for: ${trimmed}`, "info");
		} else {
			ctx.ui.notify(
				"Design-Plan mode enabled (Grill-me + Design Principles + Design Doc only). Run '/design-plan approve' when ready to implement.",
				"info",
			);
		}
	}

	pi.registerCommand("design-plan", {
		description: "Toggle or control Design-Plan mode (Grill-me + SRP/OCP/DIP/design-python-code + Design Doc write-only guard + subagent/hunk review).",
		handler: handleDesignPlanCommand,
	});

	pi.registerCommand("dplan", {
		description: "Alias for /design-plan.",
		handler: handleDesignPlanCommand,
	});
}

function freshState(): DesignPlanState {
	return {
		enabled: false,
		designDocPaths: [],
	};
}

function normalizeRelativePath(rawPath: string, cwd: string): string {
	if (!rawPath) return "";
	const abs = isAbsolute(rawPath) ? resolve(rawPath) : resolve(cwd, rawPath);
	const rel = relative(cwd, abs).replace(/\\/g, "/");
	return rel.startsWith("..") ? abs.replace(/\\/g, "/") : rel;
}

function isAllowedDesignDocPath(rawPath: string, cwd: string): boolean {
	const trimmed = rawPath.trim();
	if (!trimmed) return false;
	const normalized = normalizeRelativePath(trimmed, cwd);
	if (!normalized.toLowerCase().endsWith(".md")) return false;
	const base = normalized.split("/").pop()?.toLowerCase() ?? "";
	if (base === "readme.md" || base === "agents.md" || base === "claude.md") {
		return false;
	}
	return ALLOWED_DOC_PATH_PATTERNS.some((pattern) => pattern.test(normalized));
}

function findBlockedDesignPlanBashReason(command: string): string | undefined {
	const normalized = command
		.replace(/\\\n/g, " ")
		.replace(/\b\d*>\s*\/dev\/null\b/g, " ")
		.replace(/\b\d+>&\d+\b/g, " ")
		.replace(/=>/g, " ")
		.replace(/\s+/g, " ")
		.trim();
	if (!normalized) return undefined;
	for (const [pattern, reason] of BLOCKED_BASH_PATTERNS) {
		if (pattern.test(normalized)) return reason;
	}
	if (/(?:^|[^>=])(?:>>|>)\s*[^&|\s]/.test(normalized)) {
		return "Shell output redirection to files is blocked during design-plan mode.";
	}
	return undefined;
}

function buildDesignPlanContract(state: DesignPlanState): string {
	const topicLine = state.topic ? `対象テーマ: ${state.topic}\n` : "";
	const docsLine = state.designDocPaths.length > 0
		? `現在の Design Doc: ${state.designDocPaths.length > 0 ? state.designDocPaths.join(", ") : ""}\n`
		: "";
	return `[DESIGN PLAN MODE CONTRACT]
現在 Design-Plan モードが有効です。実装コードの編集はロックされており、Design Doc（Markdown）の作成・更新のみ許可されています。
${topicLine}${docsLine}
以下の4ステップを厳守して進めてください：
1. **調査と Grill-me 対話**:
   - 関連コード・スキーマ・既存ドキュメント・連携先リポジトリ（dbt の層制約等）・tk の記録を並列で調査する。
   - 前提の抜け漏れ・文書の単位（1本にまとめるか等）・誰がどうなるためのアプリか・レイヤー境界・トレードオフに未確定事項がある場合は、文章で曖昧に聞くのではなく \`ask_user_question\` ツール（未定義の略称 \`課題F\`・\`d001\` 等は使わない）で構造化して質問する。
2. **設計原則と変更シナリオの適用**:
   - SRP（単一責務）・OCP・DIP、変更局所性を検証する。
   - Python の設計が含まれる場合は \`/Users/solution/.pi/agent/skills/design-python-code/SKILL.md\`、Design Doc 執筆時は \`/Users/solution/.pi/agent/skills/write-design-doc/SKILL.md\` を read して基準を適用する。
   - **Code design** では、\`design-python-code\` の標準5層構造に従い、(1) 共有データモデル（\`models/\`）とモジュール内部型の分離、(2) 専門モジュール群の独立（互いに import しない）とトップレベル階層の粒度統一（1か所からしか使われない下位部品のトップレベル配置の禁止）、(3) 外部 I/O を担う \`infra/\` と、複数モジュールを順番に呼ぶ手続きを担う \`use_cases/\` の分離、(4) 動詞1つの細切れファイル（\`apply.py\`・\`recommend.py\` 等）や多義語・標準用語と衝突する曖昧な名前（\`model.py\`・\`table.py\`・\`utils.py\` 等）の禁止（『リーダブルコード』の命名原則）、(5) 代表的な変更シナリオ（5〜7個）ごとの変更局所性を検証する。
3. **Design Doc の作成・更新と全文読み直し（必須）**:
   - 詳細設計書ではなく合意・判断用の Design Doc として、以下の6節を含めて Markdown ファイルに書き出す：
     - Context and Scope（目的 → 現状の課題 → 解決策の範囲の順）
     - Goals and Non-goals（Goal は「誰が・何を見て・何を判断/操作でき・どういう状態になるか」の主語・述語のある文で書く。頼まれていない将来のエンハンスは入れない）
     - The Actual Design（System-context diagram [Mermaid] ＋ Code design ＋ 判断の理由とトレードオフ。「失うもの」「使う人」「解決の道筋」「作るもの」は使わない）
     - Alternatives Considered（採用案・代替案の理由とトレードオフ）
     - Cross-cutting Concerns / Open Questions
   - 書き出したら完了報告の前に**必ずファイル全文を頭から read して読み直し**、主語・述語の欠落、指標（売上/粗利/客数）や用語（DPT/部門など）の揺れ・節間の前後矛盾がないか点検して修正する。
4. **レビューとハンドオフ**:
   - Design Doc を書き出したら、差分（\`hunk diff\` / \`hunk show\`）や \`/design-plan review\`（複数サブエージェントによる独立レビュー）で検証し、合意事項を \`tk\` に記録した上で、\`/design-plan approve\` 承認を待つ。`;
}

function buildKickoffPrompt(topic: string): string {
	return `Design-Plan モードで以下の設計を進めてください：
${topic}

まず既存リポジトリ・設定・連携先（dbt 等）の制約・tk タスクを調査し、判断が必要な設計上の論点（文書の単位、誰がどうなるためのアプリか、スコープ、責務境界、トレードオフ）があれば \`ask_user_question\`（Grill-me 方式、未定義の略称は使わない）で確認した上で、Design Doc（Markdown）を作成し、全文を読み直して自己点検してください。`;
}

function buildSubagentReviewPrompt(targetDocs: string): string {
	return `作成・更新した Design Doc（${targetDocs}）について、実装に入る前のレビューを実施してください：
1. \`git diff\`（および \`hunk\` コメントがあればその一覧）で Design Doc の変更箇所と未対応コメントの有無を確認する。
2. \`write-design-doc\`（必須6節、目的→課題→解決策の順序、Goalの主語・達成状態、「失うもの」「使う人」等の禁止表現なし、Mermaid 構文規則）および \`design-python-code\`（代表的な変更シナリオ5〜7個に対する変更箇所の局所性、密結合の排除）の観点で検査する。複数の独立したサブエージェント（\`fable\` / \`opus\` / \`argon\` 等）にレビューさせる場合は、こちらの懸念を先入観として渡さずに独立実行させ、**共通の指摘**と**片方だけの指摘**に分けて整理する。
3. 指摘事項を反映したら、ファイル全文を頭から読み直して用語・指標（売上/粗利/客数、DPT/部門など）の揺れや前後矛盾がないか確かめ、実装に進んでよいか（\`/design-plan approve\`）を報告する。`;
}

function buildApproveImplementationPrompt(targetDocs: string): string {
	return `Design Doc（${targetDocs}）の設計が承認され、Design-Plan モードのコード編集ロックを解除しました。
承認された Design Doc の「作る順番（実装ステップ）」と設計境界に従い、また \`tk\` に決定事項（decision）と進捗を記録しながら実装とテストを進めてください。`;
}

export const __test = {
	isAllowedDesignDocPath,
	findBlockedDesignPlanBashReason,
	buildDesignPlanContract,
};
