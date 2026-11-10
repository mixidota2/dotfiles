import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";

const TASK_ID_RE = /\b(01[0-9A-HJKMNP-TV-Z]{10,24})\b/;
const CONTINUE_OR_TODO_RE =
	/(?:続けて|つづけて|続き|再開|今日やること|やることなんだっけ|何やれば|タスク.*(?:確認|一覧|教えて|続けて))/i;

type ExecRunner = (cmd: string, args: string[], cwd: string) => string;

function defaultRunner(cmd: string, args: string[], cwd: string): string {
	return execFileSync(cmd, args, {
		cwd,
		encoding: "utf8",
		timeout: 2500,
		stdio: ["ignore", "pipe", "ignore"],
	}).trim();
}

export default function tkHandoffExtension(pi: ExtensionAPI, customRunner?: ExecRunner): void {
	const run = customRunner ?? defaultRunner;
	const injectedKeys = new Set<string>();

	pi.on("session_start", async () => {
		injectedKeys.clear();
	});

	pi.on("before_agent_start", async (event, ctx) => {
		if (event.messageType !== "user") return;
		const prompt = String(event.prompt ?? "").trim();
		if (!prompt) return;

		const intent = detectTkIntent(prompt);
		if (!intent) return;
		if (injectedKeys.has(intent.dedupeKey)) return;

		const snapshot = buildTkContextSnapshot(intent, ctx?.cwd ?? process.cwd(), run);
		if (!snapshot) return;

		injectedKeys.add(intent.dedupeKey);
		return {
			message: {
				customType: "tk-handoff-context",
				content: snapshot,
				display: false,
			},
		};
	});

	pi.registerCommand("handoff", {
		description: "Audit and record current progress/decisions/findings/next-action to tk so the session can be cleanly reset (/new).",
		handler: async (args, ctx) => {
			const cwd = ctx.cwd ?? process.cwd();
			const trimmed = args.trim();
			const explicitId = extractTaskId(trimmed);
			const snapshot = collectHandoffSnapshot(explicitId, cwd, run);
			const prompt = buildHandoffPrompt(trimmed, snapshot);
			pi.sendUserMessage(prompt, { deliverAs: "followUp" });
			ctx.ui.notify("Queued tk handoff & session wrap-up prompt.", "info");
		},
	});

	pi.registerCommand("tk", {
		description: "Select an active tk task to continue, or pass a task ID / filter to resume immediately.",
		handler: async (args, ctx) => {
			const cwd = ctx.cwd ?? process.cwd();
			const trimmed = args.trim();
			const explicitId = extractTaskId(trimmed);
			if (explicitId) {
				pi.sendUserMessage(`${explicitId} これ続けて`, { deliverAs: "followUp" });
				ctx.ui.notify(`Queued continuation for ${explicitId}.`, "info");
				return;
			}

			const listOut = safeRun(run, "tk", ["list", "--all"], cwd);
			if (!listOut) {
				ctx.ui.notify("tk list returned no tasks or tk is unavailable.", "warning");
				return;
			}

			const tasks = parseTkTaskLines(listOut);
			if (tasks.length === 0) {
				ctx.ui.notify("No active tk tasks found.", "info");
				return;
			}

			if (!ctx.hasUI) {
				const top = tasks[0];
				pi.sendUserMessage(`${top.id} これ続けて`, { deliverAs: "followUp" });
				return;
			}

			const labels = tasks.map((t) => `${t.id} [${t.status}] ${t.summary}`);
			labels.push("Cancel");
			const choice = await ctx.ui.select("再開する tk タスクを選択", labels);
			if (!choice || choice === "Cancel") return;
			const chosenId = extractTaskId(choice);
			if (chosenId) {
				pi.sendUserMessage(`${chosenId} これ続けて`, { deliverAs: "followUp" });
				ctx.ui.notify(`Queued continuation for ${chosenId}.`, "info");
			}
		},
	});
}

type TkIntent = {
	kind: "task_id" | "general";
	taskId?: string;
	keyword?: string;
	dedupeKey: string;
};

function extractTaskId(text: string): string | undefined {
	return text.match(TASK_ID_RE)?.[1];
}

function detectTkIntent(prompt: string): TkIntent | null {
	const taskId = extractTaskId(prompt);
	if (taskId) {
		return {
			kind: "task_id",
			taskId,
			dedupeKey: `id:${taskId}`,
		};
	}
	if (!CONTINUE_OR_TODO_RE.test(prompt)) {
		return null;
	}
	const keyword = extractProjectKeyword(prompt);
	return {
		kind: "general",
		keyword,
		dedupeKey: `general:${keyword ?? "all"}`,
	};
}

function extractProjectKeyword(prompt: string): string | undefined {
	const normalized = prompt.replace(/miiru/gi, "miru");
	const m = normalized.match(/\b(miru|kiku|obt|dbt|orca|herdr)\b/i) ?? normalized.match(/(棚割|イントラ|交通費)/);
	return m ? m[1].toLowerCase() : undefined;
}

function safeRun(run: ExecRunner, cmd: string, args: string[], cwd: string): string {
	try {
		return run(cmd, args, cwd);
	} catch {
		return "";
	}
}

type ParsedTask = {
	id: string;
	status: string;
	summary: string;
};

function parseTkTaskLines(listOut: string): ParsedTask[] {
	const tasks: ParsedTask[] = [];
	for (const line of listOut.split(/\r?\n/)) {
		const m = line.match(/^(01[0-9A-HJKMNP-TV-Z]{10,24})\s+(\S+)\s+(.+)$/);
		if (m) {
			tasks.push({ id: m[1], status: m[2], summary: m[3].trim() });
		}
	}
	return tasks;
}

function buildTkContextSnapshot(intent: TkIntent, cwd: string, run: ExecRunner): string {
	const sections: string[] = [
		"[TK HANDOFF CONTEXT]",
		"ユーザーのタスク継続・確認指示に合わせて、`tk` と `git` の最新状態を事前取得しました（再度 `tk list` や `tk show` を叩き直す必要はありません。なお `tk task list` / `tk task show` というサブコマンドは存在しないため使わないでください）。",
		"主な `tk` コマンド構文: `tk list --all`, `tk show <ID> --logs 10`, `tk log <ID> --summary \"...\" --details \"...\" --next-action \"...\"`, `tk status <ID> <todo|in_progress|in_review|done>`, `tk record add <ID> --kind <decision|finding|blocker|question|hypothesis> ...`",
	];

	if (intent.kind === "task_id" && intent.taskId) {
		const showOut = safeRun(run, "tk", ["show", intent.taskId, "--logs", "8"], cwd);
		if (!showOut) return "";
		sections.push(`### tk show ${intent.taskId} --logs 8\n${showOut}`);
	} else {
		const listOut = safeRun(run, "tk", ["list", "--all"], cwd);
		if (!listOut) return "";
		sections.push(`### tk list --all\n${listOut}`);

		const tasks = parseTkTaskLines(listOut);
		const matched = intent.keyword
			? tasks.filter((t) => t.summary.toLowerCase().includes(intent.keyword!))
			: tasks.filter((t) => t.status === "in_progress");
		const targetIds = (matched.length > 0 ? matched : tasks.filter((t) => t.status === "in_progress"))
			.slice(0, 2)
			.map((t) => t.id);

		for (const id of targetIds) {
			const showOut = safeRun(run, "tk", ["show", id, "--logs", "6"], cwd);
			if (showOut) {
				sections.push(`### tk show ${id} --logs 6\n${showOut}`);
			}
		}
	}

	const gitStatus = safeRun(run, "git", ["status", "-s"], cwd);
	if (gitStatus) {
		sections.push(`### git status -s\n${gitStatus}`);
	}

	return sections.join("\n\n");
}

function collectHandoffSnapshot(explicitId: string | undefined, cwd: string, run: ExecRunner): string {
	const parts: string[] = [];
	const listOut = safeRun(run, "tk", ["list", "--all"], cwd);
	if (listOut) parts.push(`### tk list --all\n${listOut}`);

	const targetIds: string[] = [];
	if (explicitId) {
		targetIds.push(explicitId);
	} else if (listOut) {
		for (const task of parseTkTaskLines(listOut)) {
			if (task.status === "in_progress") targetIds.push(task.id);
		}
	}

	for (const id of targetIds.slice(0, 2)) {
		const showOut = safeRun(run, "tk", ["show", id, "--logs", "5"], cwd);
		if (showOut) parts.push(`### tk show ${id} --logs 5\n${showOut}`);
	}

	const gitStatus = safeRun(run, "git", ["status", "-s"], cwd);
	if (gitStatus) parts.push(`### git status -s\n${gitStatus}`);

	return parts.join("\n\n");
}

function buildHandoffPrompt(userNote: string, snapshot: string): string {
	const noteSection = userNote ? `\nユーザーからの補足・対象指定:\n${userNote}\n` : "";
	return `現在のセッションを区切って新しいセッション（/new）へ引き継げるよう、tk（tasukura）の記録と監査を行ってください。${noteSection}
手順:
1. 今回のセッションで実施した進捗・検証結果・残課題を整理し、対象タスクに \`tk log <ID> --summary "..." --details "..." --next-action "..."\` で記録する（ステータス変更が必要なら \`tk status <ID> <todo|in_progress|in_review|done>\` も実行）。
2. 新たに確定した設計判断（\`decision\`）、判明した事実（\`finding\`）、ブロッカー（\`blocker\`）、未決の問い（\`question\`）、仮説（\`hypothesis\`）があれば \`tk record add\` で追加し、解消・更新された既存 record があれば \`resolve\` / \`supersede\` / \`obsolete\` する。
3. 次の新しいセッションで \`<ID> これ続けて\` と伝えるだけで即座に再開できるよう、現在の状態と次のアクションを簡潔に報告する。

現在の tk / git 状態:
${snapshot || "(取得なし)"}`;
}

export const __test = {
	detectTkIntent,
	extractTaskId,
	parseTkTaskLines,
	buildTkContextSnapshot,
	buildHandoffPrompt,
};
