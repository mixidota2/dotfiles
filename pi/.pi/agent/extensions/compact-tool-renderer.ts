import type { ExtensionAPI, ToolRenderContext, ToolRenderResultOptions } from "@earendil-works/pi-coding-agent";
import { Text } from "@earendil-works/pi-tui";
import { homedir } from "node:os";

const HOME = homedir();
const MAX_CMD_Preview_CHARS = 88;
const MAX_ERROR_LINES = 5;
const IMAGE_PATH_RE = /\.(?:png|jpe?g|gif|webp|bmp|svg)$/i;

function isImagePath(rawPath: unknown): boolean {
	return typeof rawPath === "string" && IMAGE_PATH_RE.test(rawPath.trim());
}

function hasImageContent(result: { content?: Array<{ type?: string; mimeType?: string }> } | undefined): boolean {
	if (!result || !Array.isArray(result.content)) return false;
	return result.content.some((part) => part?.type === "image");
}

function extractImageSummary(result: { content?: Array<{ type?: string; text?: string; mimeType?: string }> } | undefined): string {
	const text = extractText(result);
	if (text) return text;
	const img = result?.content?.find((part) => part?.type === "image");
	return img?.mimeType ? `Read image file [${img.mimeType}]` : "Read image file";
}

function shortenPath(rawPath: unknown): string {
	const value = typeof rawPath === "string" ? rawPath.trim() : "";
	if (!value) return "…";
	if (value.startsWith(HOME)) return `~${value.slice(HOME.length)}`;
	return value;
}

function summarizeCommand(rawCommand: unknown): string {
	const value = typeof rawCommand === "string" ? rawCommand.trim() : "";
	if (!value) return "…";
	const lines = value.split("\n").map((line) => line.trim()).filter(Boolean);
	const first = (lines[0] ?? "").replace(/\s+/g, " ");
	const suffix = lines.length > 1 ? ` (+${lines.length - 1} lines)` : "";
	const shortened = first.length > MAX_CMD_Preview_CHARS
		? `${first.slice(0, MAX_CMD_Preview_CHARS - 1)}…`
		: first;
	return `${shortened}${suffix}`;
}

function formatDuration(durationMs: number | undefined): string {
	if (typeof durationMs !== "number" || !Number.isFinite(durationMs)) return "";
	if (durationMs < 1000) return ` ${Math.round(durationMs)}ms`;
	return ` ${(durationMs / 1000).toFixed(1)}s`;
}

function extractText(result: { content?: Array<{ type?: string; text?: string }> } | undefined): string {
	if (!result || !Array.isArray(result.content)) return "";
	return result.content
		.filter((part) => part?.type === "text" && typeof part.text === "string")
		.map((part) => part.text!)
		.join("\n")
		.trim();
}

function renderStatusBadge(theme: any, context: ToolRenderContext): string {
	if (context.isPartial) return theme.fg("muted", " …");
	const dur = theme.fg("muted", formatDuration(context.durationMs));
	if (context.isError) return `${theme.fg("error", " ✗")}${dur}`;
	return `${theme.fg("success", " ✓")}${dur}`;
}

function renderErrorPreview(result: any, options: ToolRenderResultOptions, theme: any, context: ToolRenderContext): Text {
	if (options.isPartial || !context.isError) {
		return new Text("", 0, 0);
	}
	const text = extractText(result);
	if (!text) return new Text(theme.fg("error", "Failed"), 0, 0);
	const lines = text.split("\n").map((line) => line.trimEnd()).filter(Boolean);
	const tail = lines.slice(-MAX_ERROR_LINES);
	const prefix = lines.length > MAX_ERROR_LINES ? [theme.fg("muted", `… (${lines.length - MAX_ERROR_LINES} earlier lines, Ctrl+O to expand)`)] : [];
	return new Text([...prefix, ...tail.map((line) => theme.fg("error", line))].join("\n"), 0, 0);
}

const PI_PROMPT_GUIDELINES = [
	"Before starting tool calls (reads, searches, bash commands, edits, or subagents) on the first turn of a request, always output a brief 1-2 sentence response telling the user what you are about to check or do (like ChatGPT), then batch independent tool calls in parallel without narrating every intermediate step.",
	"When the user asks for a proposal, design, opinion, or consultation without explicitly asking to implement/edit files, do not run edit/write or mutating bash commands — present the proposal and confirm first.",
	"Use codemode only when chaining multiple dependent tool calls or filtering large outputs in JavaScript; use direct tool calls for single operations.",
	"Before using edit, read the target section and specify a small, unique oldText matching whitespace and newlines exactly.",
];

function workingMessageForTool(toolName: string, args: any): string {
	if (toolName === "read" || toolName === "edit" || toolName === "write") {
		return `${toolName}: ${shortenPath(args?.path)}`;
	}
	if (toolName === "bash" || toolName === "powershell") {
		return `$ ${summarizeCommand(args?.command)}`;
	}
	if (toolName === "web_search" || toolName === "web_fetch" || toolName === "grep" || toolName === "find" || toolName === "ls") {
		const target = args?.query ?? args?.url ?? args?.pattern ?? args?.path ?? "";
		return `${toolName}: ${summarizeCommand(target)}`;
	}
	if (toolName === "codemode") {
		return "codemode: running script…";
	}
	return `${toolName}…`;
}

function formatToolSummary(toolName: string, args: any): string {
	if (toolName === "read" || toolName === "edit" || toolName === "write") {
		const target = shortenPath(args?.path);
		const range = toolName === "read" && (args?.offset || args?.limit)
			? `:${args?.offset ?? 1}${args?.limit ? `+${args.limit}` : ""}`
			: "";
		return `${toolName} ${target}${range}`;
	}
	if (toolName === "bash" || toolName === "powershell") {
		return `$ ${summarizeCommand(args?.command)}`;
	}
	const target = args?.query ?? args?.url ?? args?.pattern ?? args?.path ?? "";
	return target ? `${toolName} ${summarizeCommand(target)}` : toolName;
}

const QUIET_TOOLS = new Set([
	"read",
	"edit",
	"write",
	"bash",
	"powershell",
	"grep",
	"find",
	"ls",
	"web_search",
	"web_fetch",
	"codemode",
	"lens_diagnostics",
	"symbol_search",
	"project_report",
	"module_report",
	"read_symbol",
	"read_enclosing",
	"effective_config",
]);

export default function compactToolRenderer(pi: ExtensionAPI): void {
	pi.on("before_agent_start", async (event) => {
		const guidelines = event.systemPromptOptions.promptGuidelines;
		for (const item of PI_PROMPT_GUIDELINES) {
			if (!guidelines.includes(item)) {
				guidelines.push(item);
			}
		}
	});

	pi.on("tool_execution_start", async (event, ctx) => {
		if (!ctx.hasUI) return;
		ctx.ui.setWorkingMessage(workingMessageForTool(event.toolName, event.args));
	});

	pi.on("tool_execution_end", async (_event, ctx) => {
		if (!ctx.hasUI) return;
		ctx.ui.setWorkingMessage("考えています…");
	});

	pi.registerToolRenderer((toolName, next) => {
		const base = next();
		if (!QUIET_TOOLS.has(toolName)) {
			return base;
		}

		return {
			renderShell: "self",
			renderCall(args: any, theme: any, context: ToolRenderContext) {
				const imageRead = toolName === "read" && isImagePath(args?.file_path ?? args?.path);
				if (context.expanded || imageRead) {
					if (base?.renderCall) return base.renderCall(args, theme, context);
					return new Text(
						`${theme.fg("toolTitle", theme.bold(formatToolSummary(toolName, args)))}${renderStatusBadge(theme, context)}`,
						0,
						0,
					);
				}
				if (context.isError) {
					return new Text(
						`${theme.fg("error", "✗ ")}${theme.fg("toolTitle", theme.bold(formatToolSummary(toolName, args)))}${theme.fg("muted", formatDuration(context.durationMs))}`,
						0,
						0,
					);
				}
				return new Text("", 0, 0);
			},
			renderResult(result: any, options: ToolRenderResultOptions, theme: any, context: ToolRenderContext) {
				const imageRead = toolName === "read" && (hasImageContent(result) || isImagePath((context as any)?.args?.file_path ?? (context as any)?.args?.path));
				if (options.expanded) {
					if (base?.renderResult) return base.renderResult(result, options, theme, context);
					return new Text(theme.fg("toolOutput", extractText(result)), 0, 0);
				}
				if (imageRead && !context.isError) {
					return new Text(theme.fg("muted", extractImageSummary(result)), 0, 0);
				}
				return renderErrorPreview(result, options, theme, context);
			},
		};
	});
}

export const __test = {
	isImagePath,
	hasImageContent,
	extractImageSummary,
	formatToolSummary,
	workingMessageForTool,
};
