/**
 * dsh-whale-bridge — a read-only DSH Host plugin that projects the running
 * agent's coarse work state for the Big-Fat-Fish desktop pet.
 *
 * What it does (and nothing else):
 *  1. listens to the DSH durable session firehose (`ctx.on("session/event")`),
 *  2. reduces events through ./status.js into a tiny public state object,
 *  3. exposes it as `GET /api/whale/status` on the loopback web server, and
 *  4. atomically writes the same object to a fixed status file:
 *     `%USERPROFILE%\.dsh\whale-bridge\status.json` by default.
 *
 * The Python pet can poll the file without knowing the random DSH GUI port;
 * the HTTP route is for verification and future consumers.
 *
 * Privacy: this module never forwards prompts, tool arguments, tool output,
 * file paths, todo text, goal objectives, command args or approval reasons.
 * See ./status.js for the allow-list.
 *
 * Configuration (all optional, supplied as the Cordis plugin `config`):
 *   statusFile  — absolute path of the JSON status file.
 *   heartbeatMs — rewrite interval while idle; defaults to 5000 ms.
 *
 * Cordis 4.0.5 / DSH 0.2.1-rc.3 plugin shape: named exports `apply`, `inject`,
 * `name` (same convention as dsh-whale-pet). No runtime dependency beyond Node
 * built-ins, so it installs through the profile's pnpm without peers.
 */

import { randomBytes } from "node:crypto";
import { promises as fs } from "node:fs";
import { homedir } from "node:os";
import { dirname, isAbsolute, join, resolve, sep } from "node:path";

import { initialState, reduce } from "./status.js";

/** Stable Cordis plugin name; must match the loader entry id/name. */
export const name = "dsh-whale-bridge";

/** Services required before this plugin mounts. */
export const inject = ["webServer"];

/** Exact loopback route exposing the current public state. */
export const ROUTE_PATH = "/api/whale/status";

/** Default heartbeat period; the file also refreshes immediately on change. */
export const DEFAULT_HEARTBEAT_MS = 5000;

/** Lowest accepted heartbeat, so a misconfiguration cannot spin the disk. */
const MIN_HEARTBEAT_MS = 10;

/** Environment override for the status file path (useful for tests/deployments). */
export const STATUS_FILE_ENV = "DSH_WHALE_BRIDGE_STATUS_FILE";

/** Windows reports transient sharing violations on replace; retry briefly. */
const TRANSIENT_RENAME_CODES = new Set(["EACCES", "EBUSY", "EPERM"]);
const RENAME_RETRY_DELAYS_MS = [10, 25, 50, 100, 200];

/** @param {number} ms - delay in milliseconds. @returns {Promise<void>} */
function delay(ms) {
	return new Promise((resolveDelay) => setTimeout(resolveDelay, ms));
}

/**
 * The default status file location: `<DSH_HOME or ~/.dsh>/whale-bridge/status.json`.
 * @returns {string} absolute path.
 */
export function defaultStatusFile() {
	const configuredHome =
		typeof process.env.DSH_HOME === "string" ? process.env.DSH_HOME.trim() : "";
	const home = configuredHome === "" ? join(homedir(), ".dsh") : configuredHome;
	return join(home, "whale-bridge", "status.json");
}

/**
 * Expand a leading `~` and make the path absolute.
 * @param {string} file - configured path.
 * @returns {string} absolute path.
 */
function expandPath(file) {
	const home = homedir();
	let expanded = file;
	if (expanded === "~") expanded = home;
	else if (expanded.startsWith(`~${sep}`) || expanded.startsWith("~/")) {
		expanded = join(home, expanded.slice(2));
	}
	return isAbsolute(expanded) ? expanded : resolve(expanded);
}

/**
 * Resolve the effective status file: plugin config > environment > default.
 * @param {{statusFile?: unknown} | undefined} config - raw Cordis plugin config.
 * @returns {string} absolute status file path.
 */
export function resolveStatusFile(config) {
	const configured =
		config !== null && typeof config === "object" && typeof config.statusFile === "string"
			? config.statusFile.trim()
			: "";
	const fromEnv =
		typeof process.env[STATUS_FILE_ENV] === "string" ? process.env[STATUS_FILE_ENV].trim() : "";
	const chosen = configured !== "" ? configured : fromEnv;
	return chosen === "" ? defaultStatusFile() : expandPath(chosen);
}

/**
 * Resolve the heartbeat period, clamped to a sane minimum.
 * @param {{heartbeatMs?: unknown} | undefined} config - raw Cordis plugin config.
 * @returns {number} period in milliseconds.
 */
export function resolveHeartbeatMs(config) {
	const raw =
		config !== null && typeof config === "object" && config.heartbeatMs !== undefined
			? Number(config.heartbeatMs)
			: Number.NaN;
	if (!Number.isFinite(raw) || raw < MIN_HEARTBEAT_MS) return DEFAULT_HEARTBEAT_MS;
	return Math.floor(raw);
}

/**
 * Write `text` to `file` atomically: unique sibling temp file with exclusive
 * create, then rename over the target. Readers only ever observe a complete
 * document. Creates the parent directory on demand.
 * @param {string} file - destination path.
 * @param {string} text - complete file content.
 * @returns {Promise<void>} resolves once the rename committed.
 */
export async function writeFileAtomic(file, text) {
	await fs.mkdir(dirname(file), { recursive: true });
	const temp = `${file}.${process.pid}.${randomBytes(4).toString("hex")}.tmp`;
	try {
		await fs.writeFile(temp, text, { encoding: "utf8", flag: "wx", mode: 0o600 });
		for (let attempt = 0; ; attempt += 1) {
			try {
				await fs.rename(temp, file);
				return;
			} catch (error) {
				const retryable = TRANSIENT_RENAME_CODES.has(error?.code);
				if (!retryable || attempt >= RENAME_RETRY_DELAYS_MS.length) throw error;
				await delay(RENAME_RETRY_DELAYS_MS[attempt]);
			}
		}
	} catch (error) {
		await fs.rm(temp, { force: true }).catch(() => {});
		throw error;
	}
}

/**
 * Serialized, coalescing, failure-contained status-file writer. Writes go
 * through one loop: a burst of event transitions leaves only the newest
 * snapshot to persist, a heartbeat can never overtake a newer state, and a
 * failed write is logged without poisoning later writes.
 * @param {string} file - status file path.
 * @param {{warn?: Function} | undefined} logger - Cordis logger (or undefined).
 * @returns {{write: (snapshot: object) => Promise<void>, flush: () => Promise<void>, close: () => void}} writer.
 */
export function createStatusWriter(file, logger) {
	const label = "[dsh-whale-bridge]";
	let latest = null;
	let hasPending = false;
	let running = false;
	let queued = Promise.resolve();
	let closed = false;

	const report = (error) => {
		try {
			logger?.warn?.(
				`${label} writing ${file} failed (${String(error?.code ?? "EIO")}): ${String(
					error?.message ?? error,
				)}`,
			);
		} catch {
			// Logging must never break the bridge.
		}
	};

	const pump = async () => {
		running = true;
		try {
			while (hasPending) {
				hasPending = false;
				const snapshot = latest;
				try {
					await writeFileAtomic(file, `${JSON.stringify(snapshot)}\n`);
				} catch (error) {
					report(error);
				}
			}
		} catch (error) {
			report(error);
		} finally {
			running = false;
		}
	};

	const write = (snapshot) => {
		if (closed) return queued;
		latest = snapshot;
		hasPending = true;
		if (!running) queued = pump();
		return queued;
	};

	return {
		write,
		flush: () => queued,
		close: () => {
			closed = true;
		},
	};
}

/**
 * Build the HTTP handler for the status route. GET/HEAD answer JSON; anything
 * else answers 405. The body is exactly the public state object.
 * @param {() => object} getState - reads the current public state.
 * @returns {(req: import("node:http").IncomingMessage, res: import("node:http").ServerResponse) => void} handler.
 */
export function createStatusHandler(getState) {
	return (req, res) => {
		const method = String(req?.method ?? "GET").toUpperCase();
		const headers = {
			"Content-Type": "application/json; charset=utf-8",
			"Cache-Control": "no-store",
			"X-Content-Type-Options": "nosniff",
		};
		if (method !== "GET" && method !== "HEAD") {
			res.writeHead(405, { ...headers, Allow: "GET, HEAD" });
			res.end(`${JSON.stringify({ ok: false, error: "method-not-allowed" })}\n`);
			return;
		}
		let body;
		try {
			body = `${JSON.stringify(getState())}\n`;
		} catch (error) {
			res.writeHead(500, headers);
			res.end(`${JSON.stringify({ ok: false, error: "state-unavailable" })}\n`);
			return;
		}
		res.writeHead(200, headers);
		res.end(method === "HEAD" ? undefined : body);
	};
}

/**
 * Cordis plugin entry: mount the event listener, the file writer and the route.
 * Everything registered here is disposed with the plugin fiber.
 * @param {import("@deepseek-ai/cordis").Context} ctx - plugin context (requires `webServer`).
 * @param {{statusFile?: string, heartbeatMs?: number} | undefined} config - plugin config.
 * @returns {void}
 */
export function apply(ctx, config) {
	const statusFile = resolveStatusFile(config);
	const heartbeatMs = resolveHeartbeatMs(config);
	const logger = ctx.logger;
	const writer = createStatusWriter(statusFile, logger);

	let state = initialState();

	/** Publish the next state: an actual transition writes immediately. */
	const publish = (next) => {
		if (next === state) return;
		state = next;
		writer.write(state);
	};

	// The durable session firehose. A bad event must never disturb the agent.
	ctx.on("session/event", (_session, event) => {
		try {
			if (event === null || typeof event !== "object") return;
			if (typeof event.type !== "string") return;
			const at = Number.isFinite(event.time) ? Number(event.time) : Date.now();
			publish(reduce(state, event, at));
		} catch (error) {
			try {
				logger?.warn?.(
					`[dsh-whale-bridge] session/event rejected: ${String(error?.message ?? error)}`,
				);
			} catch {
				// Containment only.
			}
		}
	});

	// Status file: initial snapshot, change-driven writes via publish(), and a
	// heartbeat so the pet can tell "DSH still running" from "DSH gone".
	ctx.effect(() => {
		writer.write(state);
		const timer = setInterval(() => {
			writer.write(state);
		}, heartbeatMs);
		timer.unref?.();
		return async () => {
			clearInterval(timer);
			// A stopped harness is not "working": leave the pet in a resting pose.
			await writer.write({
				...state,
				state: "idle",
				approvalOpen: false,
				updatedAt: Date.now(),
			});
			writer.close();
			await writer.flush();
		};
	}, "dsh-whale-bridge: status file + heartbeat");

	// Loopback HTTP projection of the same object.
	ctx.effect(
		() =>
			ctx.webServer.register({
				kind: "exact",
				path: ROUTE_PATH,
				handler: createStatusHandler(() => state),
			}),
		`dsh-whale-bridge: ${ROUTE_PATH} route`,
	);
}
