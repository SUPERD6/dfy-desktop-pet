/**
 * dsh-whale-bridge — pure session-event → public pet-state reducer.
 *
 * The reducer is the ONLY place that reads DSH session events. It copies out
 * nothing but an allow-list of coarse facts:
 *
 *   ok            — the bridge instance considers itself healthy (always true;
 *                   the file writer reports its own failures through the DSH log)
 *   state         — one of idle | running | tool | success | failure | approval
 *   lastTool      — sanitized tool *name* only (never arguments, never output)
 *   doneCount     — completed turns since this plugin started
 *   errorCount    — failed tool results + failed turns since this plugin started
 *   approvalOpen  — whether an approval question is currently pending
 *   updatedAt     — epoch milliseconds of the last state change
 *   version       — public schema version of this object
 *
 * Privacy contract (hard requirement): prompts, tool arguments, tool output,
 * file paths, todo text, goal objectives, command args and approval reasons
 * MUST NEVER leave this module. Only the fields above are ever produced.
 */

/** Public schema version of the state object. */
export const VERSION = 1;

/** Every legal `state` value. */
export const STATE_VALUES = Object.freeze([
	"idle",
	"running",
	"tool",
	"success",
	"failure",
	"approval",
]);

/** The exact public key set, in serialization order. */
export const STATE_KEYS = Object.freeze([
	"ok",
	"state",
	"lastTool",
	"doneCount",
	"errorCount",
	"approvalOpen",
	"updatedAt",
	"version",
]);

/** Keep a tool name short enough for a speech bubble and free of control chars. */
const MAX_TOOL_NAME_LENGTH = 64;

/**
 * Start-of-bridge public state.
 * @param {number} [now] - epoch milliseconds for `updatedAt`.
 * @returns {object} a fresh public state object.
 */
export function initialState(now = Date.now()) {
	return {
		ok: true,
		state: "idle",
		lastTool: null,
		doneCount: 0,
		errorCount: 0,
		approvalOpen: false,
		updatedAt: now,
		version: VERSION,
	};
}

/**
 * Normalize a tool name for public exposure: string only, control characters
 * collapsed to spaces, bounded length. Returns null for anything unusable.
 * @param {unknown} value - candidate name from an event.
 * @returns {string | null} safe tool name, or null.
 */
export function sanitizeToolName(value) {
	if (typeof value !== "string") return null;
	const cleaned = value.replace(/[\u0000-\u001f\u007f]/gu, " ").replace(/\s+/gu, " ").trim();
	if (cleaned === "") return null;
	return cleaned.slice(0, MAX_TOOL_NAME_LENGTH);
}

/**
 * Whether a value is exactly the public state shape (keys and types).
 * Used by tests and by the verifier to reject unexpected payloads.
 * @param {unknown} value - candidate status object.
 * @returns {boolean} true when the object satisfies the public contract.
 */
export function isPublicState(value) {
	if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
	const keys = Object.keys(value);
	if (keys.length !== STATE_KEYS.length) return false;
	if (!STATE_KEYS.every((key) => Object.prototype.hasOwnProperty.call(value, key))) return false;
	return (
		value.ok === true &&
		STATE_VALUES.includes(value.state) &&
		(value.lastTool === null || typeof value.lastTool === "string") &&
		Number.isSafeInteger(value.doneCount) &&
		value.doneCount >= 0 &&
		Number.isSafeInteger(value.errorCount) &&
		value.errorCount >= 0 &&
		typeof value.approvalOpen === "boolean" &&
		Number.isFinite(value.updatedAt) &&
		value.version === VERSION
	);
}

/**
 * Merge a whitelisted patch into the public state. Returns the original object
 * when nothing watched changed, so callers can skip a write.
 * @param {object} state - current public state.
 * @param {object} patch - watched fields to update.
 * @param {number} now - epoch milliseconds of the causing event.
 * @returns {object} next public state (maybe the same reference).
 */
function transition(state, patch, now) {
	let changed = false;
	const next = { ...state };
	for (const [key, value] of Object.entries(patch)) {
		if (next[key] !== value) {
			next[key] = value;
			changed = true;
		}
	}
	if (!changed) return state;
	next.updatedAt = now;
	return next;
}

/**
 * Reduce one `session/event` payload into the next public state.
 *
 * Only these fields are read from an event: `type`, `time`, `data.name`,
 * `data.toolName`, `data.tool`, `data.error` (presence), `data.message.isError`,
 * `data.reason.kind` (turn end) and `data.kind` (command done). Everything else
 * is structurally ignored — never copied, never stringified into the output.
 *
 * @param {object} state - current public state (never mutated).
 * @param {object} event - one session event envelope: `{type, seq, time, data}`.
 * @param {number} [now] - epoch milliseconds used when the event has no time.
 * @returns {object} next public state (may be the same reference).
 */
export function reduce(state, event, now = Date.now()) {
	if (state === null || typeof state !== "object") return initialState(now);
	if (event === null || typeof event !== "object") return state;
	const type = event.type;
	if (typeof type !== "string") return state;
	const data =
		event.data !== null && typeof event.data === "object" && !Array.isArray(event.data)
			? event.data
			: {};

	switch (type) {
		// A turn opened: the agent is working again.
		case "turn/start":
			return transition(state, { state: "running" }, now);

		// A step opened: still working; an open approval question outranks it.
		case "step/start":
			return transition(state, { state: state.approvalOpen ? "approval" : "running" }, now);

		// The model asked for a tool. Expose its NAME only.
		case "tool/call": {
			const tool = sanitizeToolName(data.name);
			return transition(state, { state: "tool", lastTool: tool ?? state.lastTool }, now);
		}

		// A tool settled. `error` (failure identity) or `message.isError` means
		// the tool failed; a successful result just returns to work.
		case "tool/result": {
			const failed = Boolean(data.error) || data.message?.isError === true;
			return failed
				? transition(
						state,
						{ state: "failure", errorCount: state.errorCount + 1 },
						now,
					)
				: transition(state, { state: "running" }, now);
		}

		// A turn closed. v0.2.1-rc.3 carries `reason.kind`; older envelopes do
		// not, and a missing reason is treated as a completed turn.
		case "turn/end": {
			const reasonKind = typeof data.reason?.kind === "string" ? data.reason.kind : undefined;
			if (reasonKind === undefined || reasonKind === "completed") {
				return transition(
					state,
					{ state: "success", doneCount: state.doneCount + 1, approvalOpen: false },
					now,
				);
			}
			if (reasonKind === "error") {
				return transition(
					state,
					{ state: "failure", errorCount: state.errorCount + 1, approvalOpen: false },
					now,
				);
			}
			// aborted / blocked / max-tokens / interrupted / forked: not a success
			// and not an error the pet should dramatize.
			return transition(state, { state: "idle", approvalOpen: false }, now);
		}

		// An approval question is pending: the agent is waiting for the human.
		case "approval/asked": {
			const tool = sanitizeToolName(data.toolName) ?? sanitizeToolName(data.tool);
			return transition(
				state,
				{ state: "approval", approvalOpen: true, lastTool: tool ?? state.lastTool },
				now,
			);
		}

		// The human answered (allowed / rejected / cancelled / unavailable):
		// the agent keeps working. The outcome itself is not exposed.
		case "approval/decided":
			return transition(state, { state: "running", approvalOpen: false }, now);

		// Slash commands run as a tool-shaped activity; expose the category only,
		// never the command name (it is user-typed) or its args/text.
		case "command/run":
			return transition(state, { state: "tool", lastTool: "command" }, now);

		case "command/done": {
			if (data.kind === "error") {
				return transition(
					state,
					{ state: "failure", errorCount: state.errorCount + 1 },
					now,
				);
			}
			return transition(state, { state: "running" }, now);
		}

		// todo/write, goal/change, user/message, request/header, … are
		// intentionally not interpreted: their payloads are user content.
		default:
			return state;
	}
}
