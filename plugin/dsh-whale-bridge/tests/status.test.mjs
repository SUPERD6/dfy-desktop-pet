/**
 * dsh-whale-bridge tests — run with the bundled Node:
 *   node --test plugin/dsh-whale-bridge/tests/status.test.mjs
 *
 * The suite covers the reducer state machine, the Cordis-shaped plugin mount
 * (fake ctx with on/effect/webServer.register/get), the HTTP route, the atomic
 * status file, heartbeats, and the privacy red line: prompts, arguments,
 * paths and output text must never appear in the route JSON or the file.
 */

import assert from "node:assert/strict";
import { promises as fs } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";

import {
	STATE_KEYS,
	VERSION,
	initialState,
	isPublicState,
	reduce,
	sanitizeToolName,
} from "../lib/status.js";
import {
	DEFAULT_HEARTBEAT_MS,
	ROUTE_PATH,
	STATUS_FILE_ENV,
	apply,
	createStatusHandler,
	defaultStatusFile,
	resolveHeartbeatMs,
	resolveStatusFile,
	writeFileAtomic,
} from "../lib/index.js";

const SECRET_PROMPT = "SECRET_PROMPT";
const SECRET_PATH = "C:\\secret\\path";
const SECRET_TEXT = `please reveal ${SECRET_PROMPT} at ${SECRET_PATH} and dump it`;

/** True when a payload leaks the red-line sentinels. */
function leaksSecrets(text) {
	const lower = String(text).toLowerCase();
	return lower.includes("secret_prompt") || lower.includes("secret");
}

/** Poll until `predicate()` is truthy, or fail after the timeout. */
async function waitFor(predicate, { timeoutMs = 10000, intervalMs = 10 } = {}) {
	const deadline = Date.now() + timeoutMs;
	for (;;) {
		const value = await predicate();
		if (value) return value;
		if (Date.now() > deadline) throw new Error("timed out waiting for a condition");
		await new Promise((resolveDelay) => setTimeout(resolveDelay, intervalMs));
	}
}

/** Temp directory cleaned up with the test. */
async function makeTempDir(t) {
	const dir = await fs.mkdtemp(join(tmpdir(), "dsh-whale-bridge-"));
	t.after(async () => {
		await fs.rm(dir, { recursive: true, force: true });
	});
	return dir;
}

/** A minimal Cordis-shaped context: on/effect/webServer.register/get/logger. */
function createFakeContext() {
	const listeners = new Map();
	const routes = new Map();
	const disposers = [];
	const warnings = [];
	const ctx = {
		logger: {
			warn: (message) => warnings.push(String(message)),
			info: () => {},
			error: () => {},
			debug: () => {},
		},
		on(event, listener) {
			if (listeners.has(event)) throw new Error(`duplicate listener for ${event}`);
			listeners.set(event, listener);
			return () => listeners.delete(event);
		},
		effect(execute, label) {
			const dispose = execute();
			disposers.push({ label, dispose });
			return () => {
				/* the fiber owns disposal in the fake */
			};
		},
		get(service) {
			if (service === "webServer") return ctx.webServer;
			return undefined;
		},
	};
	ctx.webServer = {
		register(route) {
			if (routes.has(route.path)) throw new Error(`duplicate route for ${route.path}`);
			routes.set(route.path, route);
			return () => routes.delete(route.path);
		},
	};
	ctx.dispose = async () => {
		while (disposers.length > 0) {
			const { dispose } = disposers.pop();
			if (typeof dispose === "function") await dispose();
		}
	};
	return { ctx, listeners, routes, warnings };
}

/** Mount the plugin on a fake ctx over one temp status file. */
function createHarness({ dir, heartbeatMs }) {
	const statusFile = join(dir, "status.json");
	const { ctx, listeners, routes, warnings } = createFakeContext();
	apply(ctx, { statusFile, ...(heartbeatMs === undefined ? {} : { heartbeatMs }) });
	const session = { id: "fake-session" };
	let seq = 0;
	const emit = (type, data = {}, time = Date.now()) => {
		const listener = listeners.get("session/event");
		assert.ok(listener, "session/event listener must be registered");
		listener(session, { type, seq: (seq += 1), time, data });
	};
	const readRaw = async () => fs.readFile(statusFile, "utf8");
	const readFile = async () => JSON.parse(await readRaw());
	return {
		ctx,
		routes,
		warnings,
		statusFile,
		emit,
		route: routes.get(ROUTE_PATH),
		readRaw,
		readFile,
		waitForState: (state) =>
			waitFor(async () => {
				try {
					const status = await readFile();
					return status.state === state ? status : false;
				} catch {
					return false;
				}
			}),
	};
}

/** Invoke the registered route with a fake request/response. */
function callRoute(route, method = "GET") {
	const headers = {};
	let body;
	const res = {
		statusCode: 0,
		writeHead(statusCode, responseHeaders) {
			this.statusCode = statusCode;
			Object.assign(headers, responseHeaders);
		},
		end(chunk) {
			body = chunk;
		},
	};
	route.handler({ method, url: ROUTE_PATH, headers: {} }, res);
	return { statusCode: res.statusCode, headers, body };
}

/** Assertion helper: no secret text anywhere in the payload. */
function assertNoSecrets(label, text) {
	assert.equal(leaksSecrets(text), false, `${label} must not leak prompt/path text: ${text}`);
}

test("initial state is exactly the public contract", () => {
	const state = initialState(1234);
	assert.deepEqual(Object.keys(state), [...STATE_KEYS]);
	assert.deepEqual(state, {
		ok: true,
		state: "idle",
		lastTool: null,
		doneCount: 0,
		errorCount: 0,
		approvalOpen: false,
		updatedAt: 1234,
		version: VERSION,
	});
	assert.equal(isPublicState(state), true);
});

test("reducer migrations: turn/start → tool → failure → success", () => {
	let state = initialState(1000);

	state = reduce(state, { type: "turn/start", data: { turn: 1 } }, 1001);
	assert.equal(state.state, "running");

	state = reduce(
		state,
		{ type: "tool/call", data: { name: "pwsh", arguments: '{"secret":"SECRET_PROMPT"}' } },
		1002,
	);
	assert.equal(state.state, "tool");
	assert.equal(state.lastTool, "pwsh");

	state = reduce(
		state,
		{
			type: "tool/result",
			data: {
				message: { content: [{ type: "text", text: SECRET_TEXT }], isError: true },
				error: { name: "ToolError", code: "E_TOOL", reason: SECRET_TEXT },
			},
		},
		1003,
	);
	assert.equal(state.state, "failure");
	assert.equal(state.errorCount, 1);
	assertNoSecrets("reduced failure state", JSON.stringify(state));

	state = reduce(state, { type: "turn/end", data: { turn: 1, reason: { kind: "completed" } } }, 1004);
	assert.equal(state.state, "success");
	assert.equal(state.doneCount, 1);
	assert.equal(state.approvalOpen, false);
	assert.equal(state.updatedAt, 1004);
});

test("reducer is pure and ignores malformed / unknown events", () => {
	const state = initialState(10);
	const frozen = Object.freeze(JSON.parse(JSON.stringify(state)));
	assert.equal(reduce(state, null, 20), state);
	assert.equal(reduce(state, { type: 42 }, 20), state);
	assert.equal(reduce(state, { type: "user/message", data: { content: SECRET_TEXT } }, 20), state);
	assert.equal(reduce(state, { type: "unknown/type", data: null }, 20), state);
	assert.equal(reduce(state, { type: "todo/write", data: { todos: [{ content: SECRET_TEXT }] } }, 20), state);
	assert.equal(reduce(state, { type: "goal/change", data: { objective: SECRET_TEXT } }, 20), state);
	assert.deepEqual(state, frozen);
});

test("turn/end reasons: completed → success, error → failure, aborted → idle", () => {
	let state = initialState(0);
	state = reduce(state, { type: "turn/start", data: { turn: 1 } }, 1);
	state = reduce(
		state,
		{ type: "turn/end", data: { turn: 1, reason: { kind: "error", error: { message: SECRET_TEXT } } } },
		2,
	);
	assert.equal(state.state, "failure");
	assert.equal(state.errorCount, 1);
	assert.equal(state.doneCount, 0);
	assertNoSecrets("turn error reason", JSON.stringify(state));

	state = reduce(state, { type: "turn/start", data: { turn: 2 } }, 3);
	state = reduce(
		state,
		{ type: "turn/end", data: { turn: 2, reason: { kind: "aborted", reason: { kind: "user" } } } },
		4,
	);
	assert.equal(state.state, "idle");
	assert.equal(state.doneCount, 0);
	assert.equal(state.errorCount, 1);

	// A legacy envelope without a structured reason counts as a completed turn.
	state = reduce(state, { type: "turn/start", data: { turn: 3 } }, 5);
	state = reduce(state, { type: "turn/end", data: { turn: 3 } }, 6);
	assert.equal(state.state, "success");
	assert.equal(state.doneCount, 1);
});

test("tool names are sanitized (length, control characters, non-strings)", () => {
	assert.equal(sanitizeToolName("pwsh"), "pwsh");
	assert.equal(sanitizeToolName("a".repeat(200))?.length, 64);
	assert.equal(sanitizeToolName("bad\nname\u0000with\tcontrols"), "bad name with controls");
	assert.equal(sanitizeToolName(42), null);
	assert.equal(sanitizeToolName("   "), null);

	let state = initialState(0);
	state = reduce(state, { type: "tool/call", data: { name: "x".repeat(300) } }, 1);
	assert.equal(state.lastTool.length, 64);
	state = reduce(state, { type: "tool/call", data: { name: { not: "a string" } } }, 2);
	assert.equal(state.lastTool, "x".repeat(64));
});

test("plugin mounts: turn/start → tool → tool error → turn/end success (file + route)", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());

	assert.ok(harness.route, "status route is registered");

	// Initial heartbeat snapshot lands on disk as idle.
	const initial = await waitFor(async () => {
		try {
			return await harness.readFile();
		} catch {
			return false;
		}
	});
	assert.equal(initial.state, "idle");
	assert.equal(isPublicState(initial), true);

	harness.emit("turn/start", { turn: 1 });
	const running = await harness.waitForState("running");
	assert.equal(running.doneCount, 0);

	harness.emit("tool/call", {
		turn: 1,
		step: 1,
		callId: "call-1",
		name: "pwsh",
		arguments: JSON.stringify({ prompt: SECRET_TEXT, path: SECRET_PATH }),
	});
	const tool = await harness.waitForState("tool");
	assert.equal(tool.lastTool, "pwsh");

	harness.emit("tool/result", {
		turn: 1,
		step: 1,
		message: { content: [{ type: "text", text: SECRET_TEXT }], isError: true },
		error: { name: "ToolError", code: "E_TOOL", reason: SECRET_TEXT },
	});
	const failure = await harness.waitForState("failure");
	assert.equal(failure.errorCount, 1);

	harness.emit("turn/end", { turn: 1, reason: { kind: "completed" } });
	const success = await harness.waitForState("success");
	assert.equal(success.doneCount, 1);
	assert.equal(success.errorCount, 1);

	// Route and file agree on everything except the timestamp of the last write.
	const raw = await harness.readRaw();
	const fromRoute = JSON.parse(callRoute(harness.route).body);
	assertNoSecrets("status file", raw);
	assertNoSecrets("route body", callRoute(harness.route).body);
	assert.deepEqual(
		{ ...fromRoute, updatedAt: 0 },
		{ ...success, updatedAt: 0 },
		"route JSON must equal the file snapshot",
	);
	assert.deepEqual(Object.keys(fromRoute), [...STATE_KEYS]);
});

test("tool/result failure detection also uses message.isError", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());

	harness.emit("turn/start", { turn: 1 });
	await harness.waitForState("running");
	harness.emit("tool/result", {
		turn: 1,
		step: 1,
		message: { content: [], isError: true },
	});
	const state = await harness.waitForState("failure");
	assert.equal(state.errorCount, 1);
});

test("approval lifecycle: asked → approval (open), decided → running (closed)", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());

	harness.emit("turn/start", { turn: 1 });
	await harness.waitForState("running");

	harness.emit("approval/asked", {
		id: "approval-1",
		toolName: "pwsh",
		reason: SECRET_TEXT,
	});
	const waiting = await harness.waitForState("approval");
	assert.equal(waiting.approvalOpen, true);
	assert.equal(waiting.lastTool, "pwsh");
	assertNoSecrets("approval-asked file", await harness.readRaw());

	harness.emit("approval/decided", { id: "approval-1", outcome: "rejected" });
	const resumed = await harness.waitForState("running");
	assert.equal(resumed.approvalOpen, false);
});

test("command lifecycle exposes the category only, never the command name/args/text", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());

	harness.emit("command/run", {
		commandId: "cmd-1",
		name: "feedback",
		args: SECRET_TEXT,
		source: { kind: "user" },
	});
	const tool = await harness.waitForState("tool");
	assert.equal(tool.lastTool, "command");

	harness.emit("command/done", { commandId: "cmd-1", kind: "error", text: SECRET_TEXT });
	const failure = await harness.waitForState("failure");
	assert.equal(failure.errorCount, 1);
	const raw = await harness.readRaw();
	assertNoSecrets("command file", raw);
	assert.equal(leaksSecrets(tool.lastTool), false);
});

test("privacy red team: every content-bearing event is stripped", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());

	const events = [
		["turn/start", { turn: 1 }],
		["user/message", { content: [{ type: "text", text: SECRET_TEXT }] }],
		[
			"tool/call",
			{ turn: 1, step: 1, callId: "c1", name: "read_file", arguments: `{"path":"${SECRET_PATH}"}` },
		],
		["todo/write", { todos: [{ content: SECRET_TEXT, status: "pending" }] }],
		["goal/change", { version: 1, operation: "create", goal: { objective: SECRET_TEXT } }],
		["approval/asked", { id: "a1", toolName: "pwsh", reason: SECRET_TEXT }],
		["approval/decided", { id: "a1", outcome: "allowed-once" }],
		["command/run", { commandId: "k1", name: "compact", args: SECRET_TEXT, source: { kind: "user" } }],
		["command/done", { commandId: "k1", kind: "success", text: SECRET_TEXT }],
		[
			"tool/result",
			{
				turn: 1,
				step: 1,
				message: { content: [{ type: "text", text: SECRET_TEXT }], isError: true },
				error: { name: "Boom", code: "E", reason: SECRET_TEXT },
				meta: { diff: SECRET_TEXT },
			},
		],
		["turn/end", { turn: 1, reason: { kind: "error", error: { message: SECRET_TEXT } } }],
	];
	for (const [type, data] of events) harness.emit(type, data);

	await harness.waitForState("failure");
	// Reach the complete final snapshot through a clean success transition.
	harness.emit("turn/start", { turn: 2 });
	harness.emit("turn/end", { turn: 2, reason: { kind: "completed" } });
	await harness.waitForState("success");

	const raw = await harness.readRaw();
	const body = callRoute(harness.route).body;
	assertNoSecrets("red-team file", raw);
	assertNoSecrets("red-team route", body);
	const status = JSON.parse(body);
	assert.equal(isPublicState(status), true);
	assert.deepEqual(Object.keys(status), [...STATE_KEYS]);
	assert.equal(JSON.stringify(status).includes(SECRET_PATH.replaceAll("\\", "\\\\")), false);
});

test("heartbeat rewrites the file while the state stays unchanged", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir, heartbeatMs: 40 });
	t.after(async () => harness.ctx.dispose());

	const first = await waitFor(async () => {
		try {
			return await harness.readFile();
		} catch {
			return false;
		}
	});
	const firstStat = await fs.stat(harness.statusFile);
	await waitFor(async () => {
		const now = await fs.stat(harness.statusFile);
		return now.mtimeMs > firstStat.mtimeMs;
	});
	const second = await harness.readFile();
	assert.equal(second.state, "idle");
	assert.equal(second.updatedAt, first.updatedAt, "heartbeat must not fake a state change");
});

test("writes are atomic and leave no temp files", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());

	for (let turn = 1; turn <= 5; turn += 1) {
		harness.emit("turn/start", { turn });
		harness.emit("turn/end", { turn, reason: { kind: "completed" } });
	}
	const status = await waitFor(async () => {
		try {
			const value = await harness.readFile();
			return value.doneCount === 5 ? value : false;
		} catch {
			return false;
		}
	});
	assert.equal(status.state, "success");
	assert.equal(status.doneCount, 5);

	const entries = await fs.readdir(dir);
	assert.deepEqual(entries, ["status.json"], "only the committed file may remain");
	const parsed = JSON.parse(await fs.readFile(harness.statusFile, "utf8"));
	assert.equal(isPublicState(parsed), true);
});

test("rapid events serialize: the last write always reflects the latest state", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());

	harness.emit("turn/start", { turn: 1 });
	harness.emit("tool/call", { turn: 1, step: 1, name: "pwsh", arguments: "{}" });
	harness.emit("turn/end", { turn: 1, reason: { kind: "completed" } });
	const success = await harness.waitForState("success");
	const raw = await harness.readRaw();
	assertNoSecrets("rapid file", raw);
	assert.equal(JSON.parse(raw).doneCount, 1);
	assert.equal(success.state, "success");
});

test("writeFileAtomic creates parent directories and replaces content", async (t) => {
	const dir = await makeTempDir(t);
	const target = join(dir, "nested", "deeper", "status.json");
	await writeFileAtomic(target, '{"a":1}\n');
	assert.equal(await fs.readFile(target, "utf8"), '{"a":1}\n');
	await writeFileAtomic(target, '{"a":2}\n');
	assert.equal(await fs.readFile(target, "utf8"), '{"a":2}\n');
	const entries = await fs.readdir(join(dir, "nested", "deeper"));
	assert.deepEqual(entries, ["status.json"]);
});

test("route method handling: GET/HEAD 200, POST 405", async () => {
	const status = {
		ok: true,
		state: "idle",
		lastTool: null,
		doneCount: 0,
		errorCount: 0,
		approvalOpen: false,
		updatedAt: 1,
		version: VERSION,
	};
	const handler = createStatusHandler(() => status);

	const get = callRoute({ path: ROUTE_PATH, handler }, "GET");
	assert.equal(get.statusCode, 200);
	assert.equal(get.headers["Content-Type"], "application/json; charset=utf-8");
	assert.equal(get.headers["Cache-Control"], "no-store");
	assert.deepEqual(JSON.parse(get.body), status);

	const head = callRoute({ path: ROUTE_PATH, handler }, "HEAD");
	assert.equal(head.statusCode, 200);
	assert.equal(head.body, undefined);

	const post = callRoute({ path: ROUTE_PATH, handler }, "POST");
	assert.equal(post.statusCode, 405);
	assert.equal(post.headers.Allow, "GET, HEAD");
	assert.equal(JSON.parse(post.body).ok, false);
});

test("dispose: final idle snapshot, timer stopped, route removed", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	harness.emit("turn/start", { turn: 1 });
	await harness.waitForState("running");

	await harness.ctx.dispose();
	const final = await harness.readFile();
	assert.equal(final.state, "idle");
	assert.equal(final.approvalOpen, false);
	assert.equal(harness.routes.size, 0, "route must be unregistered on dispose");
});

test("configuration: statusFile resolution and heartbeat clamping", () => {
	const custom = join(tmpdir(), "whale-bridge-custom", "status.json");
	assert.equal(resolveStatusFile({ statusFile: custom }), custom);
	assert.ok(defaultStatusFile().endsWith(join("whale-bridge", "status.json")));
	assert.equal(resolveHeartbeatMs({ heartbeatMs: 50 }), 50);
	assert.equal(resolveHeartbeatMs({ heartbeatMs: 0 }), DEFAULT_HEARTBEAT_MS);
	assert.equal(resolveHeartbeatMs({ heartbeatMs: "soon" }), DEFAULT_HEARTBEAT_MS);
	assert.equal(resolveHeartbeatMs(undefined), DEFAULT_HEARTBEAT_MS);

	const previous = process.env[STATUS_FILE_ENV];
	process.env[STATUS_FILE_ENV] = custom;
	try {
		assert.equal(resolveStatusFile(undefined), custom);
	} finally {
		if (previous === undefined) delete process.env[STATUS_FILE_ENV];
		else process.env[STATUS_FILE_ENV] = previous;
	}
});

test("malformed listener payloads cannot break the bridge", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());

	harness.emit("turn/start", { turn: 1 });
	await harness.waitForState("running");
	// The listener is contained: these shapes must not throw out of emit.
	assert.doesNotThrow(() => {
		harness.emit("tool/call", { name: undefined });
		harness.emit("tool/call", { name: 42 });
	});
	const status = await harness.waitForState("tool");
	assert.equal(status.state, "tool");
});

test("empty-but-live bridge: initial file is already written before any event", async (t) => {
	const dir = await makeTempDir(t);
	const harness = createHarness({ dir });
	t.after(async () => harness.ctx.dispose());
	const status = await waitFor(async () => {
		try {
			return await harness.readFile();
		} catch {
			return false;
		}
	});
	assert.equal(isPublicState(status), true);
	assert.equal(status.state, "idle");
});
