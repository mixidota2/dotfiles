import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

const HOME = homedir();
const MAX_RECENT_CHARS = 18_000;
const MAX_RECENT_ENTRIES = 80;
const MIN_CURRENT_SESSION_ENTRIES = 6;

type HarnessCandidate = {
	title: string;
	evidence: string;
	recommendation: string;
	why: string;
	solves: string;
	buildVsPackage: string;
	risk: string;
};

export default function harnessExtension(pi: ExtensionAPI) {
	pi.on("session_start", async (_event, ctx) => {
		ctx.ui.setWorkingMessage("考えています…");
	});

	pi.registerCommand("harness", {
		description: "Interactively analyze the current/recent session and implement selected pi harness, skill, tool, prompt, or extension improvements. With args, use them as the improvement request.",
		handler: async (args, ctx) => {
			const request = args.trim();
			const recent = buildRecentContextExcerpt(ctx, request);

			if (request) {
				const prompt = buildDirectHarnessPrompt(request, recent);
				pi.sendUserMessage(prompt, { deliverAs: "followUp" });
				ctx.ui.notify("Queued harness improvement request.", "info");
				return;
			}

			showHarnessProgress(ctx, "直近セッションを確認しています", "最近の会話から、改善の根拠になりそうな発言や詰まりを集めています。");

			if (!ctx.model) {
				clearHarnessProgress(ctx);
				pi.sendUserMessage(buildReviewHarnessPrompt(recent), { deliverAs: "followUp" });
				ctx.ui.notify("アクティブなモデルがないため、通常メッセージとして改善候補レビューをキューしました。", "warning");
				return;
			}

			let candidates: HarnessCandidate[];
			try {
				showHarnessProgress(ctx, "改善候補を考えています", "裏側でモデルを呼び出し、拡張・ツール・スキル・プロンプト等のどれで直すべきかを整理しています。");
				candidates = await generateCandidates(recent, ctx);
				showHarnessProgress(ctx, "候補を整理しています", "返ってきた候補を選択肢として表示できる形に整えています。");
			} catch (error) {
				clearHarnessProgress(ctx);
				ctx.ui.notify(`改善候補の生成に失敗しました。通常メッセージとしてレビューをキューします。${errorMessage(error)}`, "warning");
				pi.sendUserMessage(buildReviewHarnessPrompt(recent), { deliverAs: "followUp" });
				return;
			}

			if (candidates.length === 0) {
				clearHarnessProgress(ctx);
				ctx.ui.notify("直近セッションから、具体的な harness 改善候補は見つかりませんでした。", "info");
				return;
			}

			showHarnessProgress(ctx, "候補を選んでください", "生成した改善候補を表示しています。実装したいものを複数選べます。");
			const selected = await selectCandidates(candidates, ctx);
			if (selected.length === 0) {
				clearHarnessProgress(ctx);
				ctx.ui.notify("Harness 改善をキャンセルしました。", "info");
				return;
			}

			showHarnessProgress(ctx, "実装確認中", "選択した改善内容を確認しています。");
			const ok = await ctx.ui.confirm(
				"選択した Harness 改善を実装しますか？",
				formatCandidates(selected) + "\n\nこれらの改善を今から実装しますか？",
			);
			if (!ok) {
				clearHarnessProgress(ctx);
				ctx.ui.notify("Harness 改善はキューしませんでした。", "info");
				return;
			}

			clearHarnessProgress(ctx);
			pi.sendUserMessage(buildImplementationPrompt(selected, recent), { deliverAs: "followUp" });
			ctx.ui.notify(`Harness 改善を ${selected.length} 件キューしました。`, "info");
		},
	});
}

function showHarnessProgress(ctx: any, title: string, detail: string): void {
	ctx.ui.setStatus("harness", `harness: ${title}`);
	ctx.ui.setWidget("harness-progress", [
		`🛠️ ${title}`,
		`   ${detail}`,
	], { placement: "belowEditor" });
}

function clearHarnessProgress(ctx: any): void {
	ctx.ui.setStatus("harness", undefined);
	ctx.ui.setWidget("harness-progress", undefined);
}

async function selectCandidates(candidates: HarnessCandidate[], ctx: any): Promise<HarnessCandidate[]> {
	const selected: HarnessCandidate[] = [];
	while (true) {
		const remaining = candidates.filter((candidate) => !selected.includes(candidate));
		if (remaining.length === 0) return selected;
		const labels = remaining.map(candidateLabel);
		labels.push(selected.length > 0 ? `Done (${selected.length} selected)` : "Cancel");
		const choice = await ctx.ui.select(
			selected.length > 0 ? "追加で実装する harness 改善を選択" : "実装する harness 改善を選択",
			labels,
		);
		if (!choice || choice.startsWith("Done") || choice === "Cancel") return selected;
		const index = labels.indexOf(choice);
		if (index >= 0 && index < remaining.length) selected.push(remaining[index]);
	}
}

function candidateLabel(candidate: HarnessCandidate): string {
	return [
		`${candidate.title} [${candidate.recommendation}]`,
		`必要な理由: ${candidate.why}`,
		`解決できること: ${candidate.solves}`,
	].join("\n");
}

async function generateCandidates(recent: string, ctx: any): Promise<HarnessCandidate[]> {
	const auth = await ctx.modelRegistry.getApiKeyAndHeaders(ctx.model);
	if (!auth.ok || !auth.apiKey) throw new Error(auth.ok ? `No API key for ${ctx.model.provider}` : auth.error);

	const { complete } = await import("@earendil-works/pi-ai/compat");
	const response = await complete(
		ctx.model,
		{
			systemPrompt: "You analyze Pi coding-agent sessions and propose only evidence-backed harness improvements. Return JSON only.",
			messages: [{
				role: "user",
				content: [{ type: "text", text: buildCandidateJsonPrompt(recent) }],
				timestamp: Date.now(),
			}],
		},
		{ apiKey: auth.apiKey, headers: auth.headers, env: auth.env, signal: ctx.signal },
	);
	if (response.stopReason === "aborted") throw new Error("Candidate generation aborted");
	const text = response.content
		.filter((part: any) => part?.type === "text")
		.map((part: any) => part.text)
		.join("\n");
	const parsed = parseJsonObject(text) as { candidates?: unknown[] };
	if (!Array.isArray(parsed.candidates)) return [];
	return parsed.candidates.map(normalizeCandidate).filter((candidate): candidate is HarnessCandidate => !!candidate).slice(0, 6);
}

function buildCandidateJsonPrompt(recent: string): string {
	return `Review the recent/current session excerpt below and propose practical improvements to my Pi coding-agent harness.

Focus on:
- custom extensions/hooks
- custom tools
- skills
- prompt templates
- CLAUDE.md / AGENTS.md changes
- whether to use an existing public package instead of writing custom code
- user reactions, corrections, confusion, dissatisfaction, repeated friction, and implicit goals
- transparency/progress concerns such as wanting to see what is happening before edits or long-running work

Constraints:
- Propose only improvements supported by evidence in the session.
- Avoid keyword-level speculation; infer from corrections, preferences, repeated friction, and outcomes.
- Explore what the user seems unhappy about and what they are trying to accomplish, especially from their reactions.
- Include why the improvement is needed and what concrete problem it solves.
- Include implementation unit and build-vs-package recommendation.
- Include risk/maintenance notes.
- Prefer fewer, higher-quality candidates.

Return JSON only with this schema:
{
  "candidates": [
    {
      "title": "short title",
      "evidence": "session evidence",
      "recommendation": "extension | tool | skill | prompt template | context file | package | no change",
      "why": "why this improvement is needed and why this unit is appropriate",
      "solves": "what user-visible problem or dissatisfaction this solves",
      "buildVsPackage": "build custom or use public package, and why",
      "risk": "risk/maintenance notes"
    }
  ]
}

Recent session excerpt:
${recent}`;
}

function buildReviewHarnessPrompt(recent: string): string {
	return `You are helping improve my Pi coding-agent harness.

Review the recent/current session excerpt below and propose practical improvements to my Pi setup. Do not implement anything yet. Ask me to choose one or more candidates to implement. For each candidate, explain why it is needed and what user-visible problem it solves.

Recent session excerpt:
${recent}`;
}

function buildDirectHarnessPrompt(request: string, recent: string): string {
	return `You are helping improve my Pi coding-agent harness.

User request:
${request}

Use the recent/current session excerpt as context.
1. Before running any inspection or search tools, first output a brief 1-2 sentence response telling the user what you are about to check.
2. Decide the right implementation unit: extension, tool, skill, prompt template, context file, package, or no change.
3. If the user is asking for a proposal, opinion, or design discussion, or if there are multiple design options/trade-offs that need user agreement, explain the root cause and options first and confirm with \`ask_user_question\` before editing files. Otherwise, implement the selected improvement safely and concretely.
4. If code/config changes are made, run a smoke test before claiming completion.

Mandatory verification rule:
- For Pi extension/tool changes, do not stop at import/load checks. Also run a behavior-level smoke test when feasible: register the extension with a fake ExtensionAPI, call the custom tool execute function, or invoke the event/command handler with a minimal mock context.
- Report the exact smoke-test command and result.

Recent session excerpt:
${recent}`;
}

function buildImplementationPrompt(candidates: HarnessCandidate[], recent: string): string {
	return `Implement these selected Pi harness improvements and no other candidates.

Selected candidates:
${formatCandidates(candidates)}

Process:
1. Briefly confirm the implementation unit and plan.
2. Implement the smallest safe change.
3. If code/config changes are made, run a smoke test before claiming completion.
4. For Pi extension/tool changes, do not stop at import/load checks. Also run a behavior-level smoke test when feasible: register the extension with a fake ExtensionAPI, call the custom tool execute function, or invoke the event/command handler with a minimal mock context.
5. Report changed files and exact smoke-test command/result.

Recent session excerpt for context:
${recent}`;
}

function formatCandidates(candidates: HarnessCandidate[]): string {
	return candidates.map((candidate, index) => `#${index + 1}\n${formatCandidate(candidate)}`).join("\n\n");
}

function formatCandidate(candidate: HarnessCandidate): string {
	return [
		`Title: ${candidate.title}`,
		`Evidence: ${candidate.evidence}`,
		`Recommendation: ${candidate.recommendation}`,
		`Why needed / unit fit: ${candidate.why}`,
		`Solves: ${candidate.solves}`,
		`Build vs package: ${candidate.buildVsPackage}`,
		`Risk: ${candidate.risk}`,
	].join("\n");
}

function normalizeCandidate(value: unknown): HarnessCandidate | null {
	if (!value || typeof value !== "object") return null;
	const obj = value as Record<string, unknown>;
	const candidate = {
		title: stringField(obj.title),
		evidence: stringField(obj.evidence),
		recommendation: stringField(obj.recommendation),
		why: stringField(obj.why),
		solves: stringField(obj.solves),
		buildVsPackage: stringField(obj.buildVsPackage),
		risk: stringField(obj.risk),
	};
	if (!candidate.title || !candidate.evidence || !candidate.recommendation || !candidate.why || !candidate.solves) return null;
	return candidate;
}

function stringField(value: unknown): string {
	return typeof value === "string" ? value.trim() : "";
}

function parseJsonObject(text: string): Record<string, unknown> {
	const trimmed = text.trim().replace(/^```(?:json)?\s*/i, "").replace(/```$/i, "").trim();
	try {
		return JSON.parse(trimmed) as Record<string, unknown>;
	} catch {
		const start = trimmed.indexOf("{");
		const end = trimmed.lastIndexOf("}");
		if (start >= 0 && end > start) return JSON.parse(trimmed.slice(start, end + 1)) as Record<string, unknown>;
		throw new Error(`Model did not return JSON: ${trimmed.slice(0, 300)}`);
	}
}

function clampSection(text: string, maxChars: number): string {
	const trimmed = text.trim();
	if (trimmed.length <= maxChars) return trimmed;
	return `…(truncated)\n${trimmed.slice(trimmed.length - maxChars)}`;
}

const IGNORED_ENTRY_TYPES = new Set([
	"session",
	"model_change",
	"thinking_level_change",
	"attachment",
	"api-request",
	"api-request-blob",
	"file-history-snapshot",
	"last-prompt",
	"mode",
	"atis-latch",
	"ai-title",
	"system",
	"queue-operation",
]);

const NOISE_TEXT_PREFIXES = [
	"<local-command-caveat>",
	"<command-name>",
	"<command-message>",
	"<local-command-stdout>",
	"Base directory for this skill:",
	"<task-notification>",
	"Another Claude session sent a message:",
	"<total_tokens>",
];

function isNoiseText(text: string): boolean {
	const trimmed = text.trim();
	if (!trimmed) return true;
	return NOISE_TEXT_PREFIXES.some((prefix) => trimmed.startsWith(prefix));
}

function buildRecentContextExcerpt(ctx: any, request = ""): string {
	const currentEntries = getRecentEntries(ctx);
	const currentExcerpt = buildRecentSessionExcerpt(currentEntries);
	const sections: string[] = [];
	if (currentExcerpt) {
		sections.push(`## Current Session\n${clampSection(currentExcerpt, 8_000)}`);
	}

	const currentSessionFile = typeof ctx?.sessionManager?.getSessionFile === "function"
		? ctx.sessionManager.getSessionFile()
		: undefined;
	const cwd = typeof ctx?.cwd === "string" && ctx.cwd ? ctx.cwd : process.cwd();
	const preferClaude = /claude|cladue|クロード/i.test(request);

	if (preferClaude || countMessageEntries(currentEntries) < MIN_CURRENT_SESSION_ENTRIES) {
		const fallbackExcerpt = collectRecentWorkspaceSessionsExcerpt(cwd, currentSessionFile, HOME, { preferClaude });
		if (fallbackExcerpt) {
			sections.push(`## Recent Workspace Sessions\n${clampSection(fallbackExcerpt, preferClaude ? 12_000 : 6_000)}`);
		}
	}

	const tkExcerpt = collectTkExcerpt(cwd);
	if (tkExcerpt) {
		sections.push(`## Recent Task Records (tk)\n${clampSection(tkExcerpt, 3_500)}`);
	}

	return sections.join("\n\n").trim();
}

function countMessageEntries(entries: unknown[]): number {
	let count = 0;
	for (const entry of entries) {
		const e = entry as any;
		const role = e?.message?.role ?? e?.role;
		if (role === "user" || role === "assistant") count += 1;
	}
	return count;
}

function piSessionDirForCwd(cwd: string, homeDir = HOME): string {
	const slug = `--${cwd.replace(/^\/+/, "").replace(/[\/\\:]+/g, "-")}--`;
	return join(homeDir, ".pi", "agent", "sessions", slug);
}

function claudeProjectDirForCwd(cwd: string, homeDir = HOME): string {
	const slug = `-${cwd.replace(/^\/+/, "").replace(/[\/\\:]+/g, "-")}`;
	return join(homeDir, ".claude", "projects", slug);
}

function listRecentJsonlFiles(dir: string, excludeFile: string | undefined, limit: number): string[] {
	if (!existsSync(dir)) return [];
	try {
		return readdirSync(dir)
			.filter((name) => name.endsWith(".jsonl"))
			.map((name) => {
				const fullPath = join(dir, name);
				try {
					return { fullPath, mtimeMs: statSync(fullPath).mtimeMs };
				} catch {
					return null;
				}
			})
			.filter((item): item is { fullPath: string; mtimeMs: number } => !!item && item.fullPath !== excludeFile)
			.sort((a, b) => b.mtimeMs - a.mtimeMs)
			.slice(0, limit)
			.map((item) => item.fullPath);
	} catch {
		return [];
	}
}

function listRecentPiFilesAcrossWorkspaces(
	homeDir: string,
	excludeFiles: Set<string>,
	limit: number,
	maxAgeMs = 14 * 24 * 60 * 60 * 1000,
): string[] {
	const sessionsRoot = join(homeDir, ".pi", "agent", "sessions");
	if (!existsSync(sessionsRoot)) return [];
	const cutoff = Date.now() - maxAgeMs;
	const candidates: Array<{ fullPath: string; mtimeMs: number }> = [];
	try {
		for (const wsEntry of readdirSync(sessionsRoot)) {
			if (!wsEntry.startsWith("--") || wsEntry.startsWith("--private-tmp-hakaru-")) continue;
			const wsDir = join(sessionsRoot, wsEntry);
			try {
				if (!statSync(wsDir).isDirectory()) continue;
				for (const name of readdirSync(wsDir)) {
					if (!name.endsWith(".jsonl") || name === "session.jsonl") continue;
					const fullPath = join(wsDir, name);
					if (excludeFiles.has(fullPath)) continue;
					try {
						const mtimeMs = statSync(fullPath).mtimeMs;
						if (mtimeMs >= cutoff) candidates.push({ fullPath, mtimeMs });
					} catch {
						// Ignore unreadable session files.
					}
				}
			} catch {
				// Ignore unreadable workspace dirs.
			}
		}
	} catch {
		return [];
	}
	return candidates
		.sort((a, b) => b.mtimeMs - a.mtimeMs)
		.slice(0, limit)
		.map((item) => item.fullPath);
}

function listRecentClaudeFilesAcrossProjects(
	homeDir: string,
	excludeFiles: Set<string>,
	limit: number,
	maxAgeMs = 14 * 24 * 60 * 60 * 1000,
): string[] {
	const projectsRoot = join(homeDir, ".claude", "projects");
	if (!existsSync(projectsRoot)) return [];
	const cutoff = Date.now() - maxAgeMs;
	const candidates: Array<{ fullPath: string; mtimeMs: number }> = [];
	try {
		for (const projectEntry of readdirSync(projectsRoot)) {
			if (projectEntry.startsWith("-private-tmp-hakaru-") || projectEntry === "-private-tmp") continue;
			const projectDir = join(projectsRoot, projectEntry);
			try {
				if (!statSync(projectDir).isDirectory()) continue;
				for (const name of readdirSync(projectDir)) {
					if (!name.endsWith(".jsonl")) continue;
					const fullPath = join(projectDir, name);
					if (excludeFiles.has(fullPath)) continue;
					try {
						const mtimeMs = statSync(fullPath).mtimeMs;
						if (mtimeMs >= cutoff) candidates.push({ fullPath, mtimeMs });
					} catch {
						// Ignore unreadable session files.
					}
				}
			} catch {
				// Ignore unreadable project dirs.
			}
		}
	} catch {
		return [];
	}
	return candidates
		.sort((a, b) => b.mtimeMs - a.mtimeMs)
		.slice(0, limit)
		.map((item) => item.fullPath);
}

function collectRecentToolFrictionSummary(files: string[]): string {
	const counts = new Map<string, number>();
	const bump = (label: string, n = 1) => counts.set(label, (counts.get(label) ?? 0) + n);

	for (const file of files) {
		try {
			const raw = readFileSync(file, "utf8");
			const webSearchHits = (raw.match(/allowedPartnerModelFeatures[^\n]*web_search/g) ?? []).length;
			if (webSearchHits > 0) bump("Claude WebSearch 400 (Vertex org policy)", webSearchHits);
			const sleepHits = (raw.match(/went to sleep mid-response/g) ?? []).length;
			if (sleepHits > 0) bump("System sleep mid-response", sleepHits);
			const dnsHits = (raw.match(/ENOTFOUND oauth2\.googleapis\.com/g) ?? []).length;
			if (dnsHits > 0) bump("DNS ENOTFOUND oauth2.googleapis.com", dnsHits);
			const tkHits = (raw.match(/Usage: tk\b/g) ?? []).length;
			if (tkHits > 0) bump("tk CLI usage/syntax error", tkHits);
			const partialEditHits = (raw.match(/PARTIAL APPLY|RETRYABLE — Edit/g) ?? []).length;
			if (partialEditHits > 0) bump("Edit partial-apply / retryable mismatch", partialEditHits);
			const modHits = (raw.match(/ModuleNotFoundError: No module named '[^']+'/g) ?? []).length;
			if (modHits > 0) bump("Python ModuleNotFoundError", modHits);
		} catch {
			// Ignore unreadable files.
		}
	}

	const entries = [...counts.entries()].sort((a, b) => b[1] - a[1]);
	if (entries.length === 0) return "";
	return entries.map(([k, v]) => `- ${k}: ${v} occurrence(s)`).join("\n");
}

function collectRecentWorkspaceSessionsExcerpt(
	cwd: string,
	excludeSessionFile?: string,
	homeDir = HOME,
	options?: { preferClaude?: boolean },
): string {
	const preferClaude = Boolean(options?.preferClaude);
	const localPiFiles = listRecentJsonlFiles(piSessionDirForCwd(cwd, homeDir), excludeSessionFile, preferClaude ? 2 : 3);
	const seenPi = new Set<string>(excludeSessionFile ? [excludeSessionFile, ...localPiFiles] : localPiFiles);
	const crossPiFiles = listRecentPiFilesAcrossWorkspaces(homeDir, seenPi, 3);
	const piFiles = [...localPiFiles, ...crossPiFiles];

	const localClaudeFiles = listRecentJsonlFiles(claudeProjectDirForCwd(cwd, homeDir), excludeSessionFile, preferClaude ? 3 : 2);
	const seenClaude = new Set<string>(excludeSessionFile ? [excludeSessionFile, ...localClaudeFiles] : localClaudeFiles);
	const crossProjectClaudeFiles = listRecentClaudeFilesAcrossProjects(homeDir, seenClaude, preferClaude ? 4 : 3);
	const claudeFiles = [...localClaudeFiles, ...crossProjectClaudeFiles];

	const files = preferClaude ? [...claudeFiles, ...piFiles] : [...piFiles, ...claudeFiles];
	const blocks: string[] = [];
	const perSessionChars = preferClaude ? 2_400 : 1_600;
	const perSessionEntries = preferClaude ? 24 : 16;
	const maxTotalChars = preferClaude ? 12_000 : 8_000;
	let totalChars = 0;

	const frictionSummary = collectRecentToolFrictionSummary(files);
	if (frictionSummary) {
		const summaryBlock = `#### Cross-Session Tool & API Friction\n${frictionSummary}`;
		blocks.push(summaryBlock);
		totalChars += summaryBlock.length + 2;
	}

	for (const file of files) {
		const tail = readJsonlTail(file, 48);
		const rawExcerpt = buildRecentSessionExcerpt(tail, perSessionEntries, {
			user: 800,
			assistant: 220,
			collapseConsecutiveAssistant: true,
		});
		const excerpt = clampSection(rawExcerpt, perSessionChars);
		if (excerpt) {
			const name = file.split(/[\\/]/).pop() ?? file;
			const block = `#### Session ${name}\n${excerpt}`;
			if (blocks.length > 0 && totalChars + block.length + 2 > maxTotalChars) break;
			blocks.push(block);
			totalChars += block.length + 2;
		}
	}
	return blocks.join("\n\n");
}

function collectTkExcerpt(cwd: string, runCommand?: (args: string[]) => string): string {
	const execTk = runCommand ?? ((args: string[]) =>
		execFileSync("tk", args, {
			cwd,
			encoding: "utf8",
			timeout: 2500,
			stdio: ["ignore", "pipe", "ignore"],
		}).trim());

	try {
		const listOut = execTk(["list", "--all"]);
		if (!listOut) return "";
		const inProgressIds: string[] = [];
		for (const line of listOut.split(/\r?\n/)) {
			const match = line.match(/^(01[0-9A-HJKMNP-TV-Z]{10,24})\s+in_progress\b/);
			if (match) inProgressIds.push(match[1]);
		}
		const details: string[] = [`### tk list --all\n${listOut}`];
		for (const id of inProgressIds.slice(0, 2)) {
			try {
				const showOut = execTk(["show", id, "--logs", "5"]);
				if (showOut) details.push(`### tk show ${id} --logs 5\n${showOut}`);
			} catch {
				// Ignore individual task fetch errors.
			}
		}
		return details.join("\n\n");
	} catch {
		return "";
	}
}

function getRecentEntries(ctx: any): unknown[] {
	const branch = safeEntries(() => ctx.sessionManager?.getBranch?.());
	if (branch.length > 0) return branch;

	const entries = safeEntries(() => ctx.sessionManager?.getEntries?.());
	if (entries.length > 0) return entries;

	const sessionFile = typeof ctx.sessionManager?.getSessionFile === "function" ? ctx.sessionManager.getSessionFile() : undefined;
	if (typeof sessionFile === "string" && sessionFile && existsSync(sessionFile)) {
		return readJsonlTail(sessionFile, MAX_RECENT_ENTRIES);
	}

	return [];
}

function safeEntries(readEntries: () => unknown): unknown[] {
	try {
		const entries = readEntries();
		return Array.isArray(entries) ? entries : [];
	} catch {
		return [];
	}
}

function isMeaningfulSessionEntry(entry: unknown): boolean {
	if (!entry || typeof entry !== "object") return false;
	const e = entry as any;
	if (typeof e.type === "string" && IGNORED_ENTRY_TYPES.has(e.type)) return false;
	const role = e.message?.role ?? e.role ?? e.type ?? e.customType ?? "entry";
	if (IGNORED_ENTRY_TYPES.has(String(role))) return false;
	const text = extractText(e.message?.content ?? e.content ?? e.text ?? e.summary ?? e);
	return !isNoiseText(text);
}

function readJsonlTail(path: string, maxEntries: number): unknown[] {
	const text = readFileSync(path, "utf8");
	const lines = text.trim().split(/\r?\n/).filter(Boolean);
	const collected: unknown[] = [];
	for (let i = lines.length - 1; i >= 0 && collected.length < maxEntries; i -= 1) {
		try {
			const parsed = JSON.parse(lines[i]);
			if (isMeaningfulSessionEntry(parsed)) {
				collected.push(parsed);
			}
		} catch {
			// Ignore partial/corrupt lines; session JSONL can be written concurrently.
		}
	}
	const reversed: unknown[] = [];
	for (let i = collected.length - 1; i >= 0; i -= 1) {
		reversed.push(collected[i]);
	}
	return reversed;
}

function buildRecentSessionExcerpt(
	entries: unknown[],
	maxSlice = 40,
	perMessageMaxChars?: { user: number; assistant: number; collapseConsecutiveAssistant?: boolean },
): string {
	const meaningful = entries.filter(isMeaningfulSessionEntry);
	const normalized: Array<{ role: string; text: string }> = [];
	for (const entry of meaningful) {
		const e = entry as any;
		const role = String(e.message?.role ?? e.role ?? e.type ?? e.customType ?? "entry");
		const text = extractText(e.message?.content ?? e.content ?? e.text ?? e.summary ?? e);
		if (isNoiseText(text)) continue;
		if (
			perMessageMaxChars?.collapseConsecutiveAssistant &&
			role === "assistant" &&
			normalized.at(-1)?.role === "assistant"
		) {
			normalized.pop();
			normalized.push({ role, text });
		} else {
			normalized.push({ role, text });
		}
	}

	const lines: string[] = [];
	for (const item of normalized.slice(-maxSlice)) {
		let { text } = item;
		if (perMessageMaxChars) {
			const limit = item.role === "user" ? perMessageMaxChars.user : perMessageMaxChars.assistant;
			if (text.length > limit) {
				text = `${text.slice(0, limit)}…`;
			}
		}
		lines.push(`### ${item.role}\n${text}`);
	}
	const joined = lines.join("\n\n");
	return joined.length > MAX_RECENT_CHARS ? joined.slice(joined.length - MAX_RECENT_CHARS) : joined;
}

function stripInjectedContracts(text: string): string {
	return text
		.split(/\n\[(?:TK HANDOFF CONTEXT|EVIDENCE CONTRACT|DESIGN PLAN MODE CONTRACT|PROPOSAL-ONLY BOUNDARY CONTRACT)\]/)[0]
		?.trim() ?? text.trim();
}

function extractText(value: unknown): string {
	if (typeof value === "string") return stripInjectedContracts(value);
	if (Array.isArray(value)) {
		return stripInjectedContracts(value.map((item) => {
			if (typeof item === "string") return item;
			if (item && typeof item === "object") {
				const obj = item as Record<string, unknown>;
				if ("text" in obj && typeof obj.text === "string") return obj.text;
				if (obj.type === "toolCall" && obj.name === "ask_user_question") {
					return `[toolCall: ask_user_question] ${JSON.stringify(obj.arguments ?? {}).slice(0, 300)}`;
				}
			}
			return "";
		}).filter(Boolean).join("\n"));
	}
	if (value && typeof value === "object") {
		const obj = value as Record<string, unknown>;
		if (typeof obj.text === "string") return stripInjectedContracts(obj.text);
		if (typeof obj.content === "string") return stripInjectedContracts(obj.content);
	}
	return "";
}

function errorMessage(error: unknown): string {
	return error instanceof Error ? error.message : String(error);
}

export const __test = {
	buildRecentContextExcerpt,
	buildRecentSessionExcerpt,
	collectRecentWorkspaceSessionsExcerpt,
	collectRecentToolFrictionSummary,
	listRecentPiFilesAcrossWorkspaces,
	listRecentClaudeFilesAcrossProjects,
	collectTkExcerpt,
	getRecentEntries,
	piSessionDirForCwd,
	claudeProjectDirForCwd,
};
