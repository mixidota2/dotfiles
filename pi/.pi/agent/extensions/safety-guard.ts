import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { execFileSync } from "node:child_process";
import { homedir } from "node:os";
import { extname, isAbsolute, relative, resolve } from "node:path";

const HOME = homedir();
const CODE_EXTENSIONS = new Set([
	".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css", ".scss", ".sql", ".vue", ".svelte", ".rs", ".go",
]);

export type LiveMainRepoDetector = (absPath: string, cwd: string) => { repoRoot: string; branch: string; ports: string[] } | undefined;

export default function safetyGuard(pi: ExtensionAPI, customLiveDetector?: LiveMainRepoDetector) {
	const detectLiveMain = customLiveDetector ?? defaultLiveMainRepoDetector;
	const approvedLiveMainRepos = new Set<string>();

	pi.on("session_start", async () => {
		approvedLiveMainRepos.clear();
	});

	pi.on("tool_call", async (event, ctx) => {
		if (event.toolName === "bash") {
			const input = event.input as { command?: string };
			const command = input.command ?? "";
			const hardBlock = dangerousBashReason(command);
			if (hardBlock) return { block: true, reason: hardBlock };

			const confirmReason = suspiciousBashReason(command);
			if (confirmReason) {
				if (!ctx.hasUI) return { block: true, reason: `${confirmReason} No UI available for confirmation.` };
				const ok = await ctx.ui.confirm("Safety guard", `${confirmReason}\n\nAllow this command?\n\n${command}`);
				if (!ok) return { block: true, reason: `Blocked by safety guard: ${confirmReason}` };
			}
		}

		if (event.toolName === "write" || event.toolName === "edit") {
			const input = event.input as { path?: string };
			const target = input.path;
			if (!target) return;
			const abs = normalizePath(target, ctx.cwd);
			const hardBlock = sensitivePathReason(abs);
			if (hardBlock) return { block: true, reason: hardBlock };

			const confirmReason = guardedPathReason(abs);
			if (confirmReason && process.env.PI_UNATTENDED !== "1") {
				if (!ctx.hasUI) return { block: true, reason: `${confirmReason} No UI available for confirmation.` };
				const ok = await ctx.ui.confirm("Safety guard", `${confirmReason}\n\nAllow editing this path?\n\n${abs}`);
				if (!ok) return { block: true, reason: `Blocked by safety guard: ${confirmReason}` };
			}

			const liveMain = detectLiveMain(abs, ctx.cwd);
			if (liveMain && !approvedLiveMainRepos.has(liveMain.repoRoot) && process.env.ALLOW_MAIN_LIVE_EDIT !== "1") {
				const msg = `ローカルサーバー (${liveMain.ports.join(", ")}) が稼働中の ${liveMain.branch} ブランチ（${liveMain.repoRoot}）でコードファイルを直接編集しようとしています。デモ環境を壊さないよう Orca worktree または作業ブランチでの作業を推奨します。`;
				if (!ctx.hasUI) {
					return {
						block: true,
						reason: `${msg} Create/use a git worktree first, or set ALLOW_MAIN_LIVE_EDIT=1 if editing main directly was explicitly requested.`,
					};
				}
				const ok = await ctx.ui.confirm("Live server on main branch", `${msg}\n\nこのまま ${liveMain.branch} ブランチを直接編集しますか？\n\n${abs}`);
				if (!ok) {
					return { block: true, reason: `Blocked direct edit on live ${liveMain.branch} branch (${liveMain.repoRoot}). Switch to an Orca/git worktree.` };
				}
				approvedLiveMainRepos.add(liveMain.repoRoot);
			}
		}
	});
}

function dangerousBashReason(command: string): string | undefined {
	const normalized = command.replace(/\s+/g, " ").trim();
	const patterns: Array<[RegExp, string]> = [
		[/\brm\s+-[^\n;|&]*r[^\n;|&]*f[^\n;|&]*(\s+\/|\s+~(?:\s|\/|$)|\s+\.\.?(?:\s|\/|$)|\s+\$HOME(?:\s|\/|$))/i, "Refusing recursive force delete of root/home/current/parent path."],
		[/\brm\s+-[^\n;|&]*r[^\n;|&]*f[^\n;|&]*\.git\b/i, "Refusing recursive force delete of .git."],
		[/\b(mkfs|fdisk|diskutil\s+erase|diskutil\s+partition|dd\s+if=)\b/i, "Refusing disk destructive command."],
		[/\bchmod\s+-R\s+777\b/i, "Refusing chmod -R 777."],
		[/\b(curl|wget)\b[^\n|;&]*\|\s*(sudo\s+)?(sh|bash)\b/i, "Refusing curl/wget pipe-to-shell."],
	];
	return patterns.find(([pattern]) => pattern.test(normalized))?.[1];
}

const SAFE_EPHEMERAL_RM_TARGET_RE =
	/^(?:\/tmp\/|\/private\/tmp\/|\$TMPDIR\/|\$\{TMPDIR[^}]*\}\/|[^\s;|&]*(?:\/\.mlh\/worktrees\/|\/worktrees\/pi-worktree-|\/orca\/workspaces\/))\S+$/;

function isSafeEphemeralRmCommand(normalized: string): boolean {
	const rmMatches = [...normalized.matchAll(/\brm\s+(-[A-Za-z]+\s+)+([^\n;|&]+)/g)];
	if (rmMatches.length === 0) return false;
	for (const m of rmMatches) {
		const targets = m[2].trim().split(/\s+/).map((t) => t.replace(/^["']|["']$/g, ""));
		if (targets.length === 0) return false;
		if (!targets.every((t) => SAFE_EPHEMERAL_RM_TARGET_RE.test(t) && !t.includes(".."))) {
			return false;
		}
	}
	return true;
}

function suspiciousBashReason(command: string): string | undefined {
	const normalized = command.replace(/\s+/g, " ").trim();
	if (/\bsudo\b/i.test(normalized)) return "Command uses sudo.";
	if (/\bchown\s+-R\b/i.test(normalized)) return "Command recursively changes ownership.";
	if (/\bchmod\s+-R\b/i.test(normalized)) return "Command recursively changes permissions.";
	if (/\brm\s+-[^\n;|&]*r/i.test(normalized) && !isSafeEphemeralRmCommand(normalized)) {
		return "Command recursively deletes files.";
	}
	return undefined;
}

function sensitivePathReason(abs: string): string | undefined {
	const lower = abs.toLowerCase();
	const base = abs.split(/[\\/]/).pop() ?? "";
	if (/^\.env(\.|$)/.test(base) || base === ".env") return "Refusing to edit environment/secret file.";
	if (isInside(abs, resolve(HOME, ".ssh"))) return "Refusing to edit ~/.ssh.";
	if (isInside(abs, resolve(HOME, ".aws"))) return "Refusing to edit ~/.aws.";
	if (isInside(abs, resolve(HOME, ".config", "gcloud"))) return "Refusing to edit gcloud credentials/config.";
	if (lower.includes("/node_modules/")) return "Refusing to edit node_modules.";
	if (lower.includes("/.git/")) return "Refusing to edit .git internals.";
}

function guardedPathReason(abs: string): string | undefined {
	if (abs.endsWith("package-lock.json") || abs.endsWith("pnpm-lock.yaml") || abs.endsWith("yarn.lock") || abs.endsWith("uv.lock")) return "Editing a lockfile.";
}

function normalizePath(path: string, cwd: string): string {
	if (path.startsWith("~/")) return resolve(HOME, path.slice(2));
	return isAbsolute(path) ? resolve(path) : resolve(cwd, path);
}

function isInside(path: string, parent: string): boolean {
	const rel = relative(parent, path);
	return rel === "" || (!rel.startsWith("..") && !isAbsolute(rel));
}

function isProtectedCodePath(abs: string): boolean {
	const ext = extname(abs).toLowerCase();
	if (!CODE_EXTENSIONS.has(ext)) return false;
	const normalized = abs.replace(/\\/g, "/");
	if (
		normalized.includes("/dotfiles/") ||
		normalized.includes("/memo/") ||
		normalized.includes("/docs/") ||
		normalized.includes("/.pi/") ||
		normalized.includes("/.claude/") ||
		normalized.startsWith("/tmp/") ||
		normalized.startsWith("/private/tmp/")
	) {
		return false;
	}
	return true;
}

function defaultLiveMainRepoDetector(
	absPath: string,
	cwd: string,
): { repoRoot: string; branch: string; ports: string[] } | undefined {
	if (!isProtectedCodePath(absPath)) return undefined;
	try {
		const targetDir = resolve(absPath, "..");
		const gitInfo = execFileSync(
			"git",
			["rev-parse", "--show-toplevel", "--abbrev-ref", "HEAD", "--git-dir", "--git-common-dir"],
			{ cwd: targetDir, encoding: "utf8", timeout: 1200, stdio: ["ignore", "pipe", "ignore"] },
		).trim().split(/\r?\n/);
		if (gitInfo.length < 4) return undefined;
		const [repoRoot, branch, gitDir, gitCommonDir] = gitInfo;
		if (branch !== "main" && branch !== "master") return undefined;
		// Linked git worktrees have gitDir !== gitCommonDir and are already isolated from main.
		if (resolve(targetDir, gitDir) !== resolve(targetDir, gitCommonDir)) return undefined;

		const lsofOut = execFileSync(
			"lsof",
			["-nP", "-iTCP", "-sTCP:LISTEN", "-Fpcn"],
			{ cwd, encoding: "utf8", timeout: 1500, stdio: ["ignore", "pipe", "ignore"] },
		);
		const pidPorts = new Map<string, Set<string>>();
		let curPid = "";
		for (const line of lsofOut.split(/\r?\n/)) {
			if (line.startsWith("p")) {
				curPid = line.slice(1);
			} else if (line.startsWith("n") && curPid) {
				const portMatch = line.match(/:(\d+)$/);
				const port = portMatch?.[1];
				if (port && /^(?:3000|3001|5173|5174|8000|8001|8080)$/.test(port)) {
					let portsForPid = pidPorts.get(curPid);
					if (!portsForPid) {
						portsForPid = new Set();
						pidPorts.set(curPid, portsForPid);
					}
					portsForPid.add(`:${port}`);
				}
			}
		}
		if (pidPorts.size === 0) return undefined;

		const cwdOut = execFileSync(
			"lsof",
			["-a", "-p", [...pidPorts.keys()].join(","), "-d", "cwd", "-Fn"],
			{ cwd, encoding: "utf8", timeout: 1500, stdio: ["ignore", "pipe", "ignore"] },
		);
		const matchedPorts = new Set<string>();
		curPid = "";
		for (const line of cwdOut.split(/\r?\n/)) {
			if (line.startsWith("p")) {
				curPid = line.slice(1);
			} else if (line.startsWith("n") && curPid) {
				const procCwd = line.slice(1);
				if (isInside(procCwd, repoRoot)) {
					for (const port of pidPorts.get(curPid) ?? []) matchedPorts.add(port);
				}
			}
		}
		if (matchedPorts.size === 0) return undefined;
		return { repoRoot, branch, ports: [...matchedPorts].sort((a, b) => a.localeCompare(b)) };
	} catch {
		return undefined;
	}
}

export const __test = {
	isProtectedCodePath,
	defaultLiveMainRepoDetector,
	suspiciousBashReason,
	dangerousBashReason,
};
