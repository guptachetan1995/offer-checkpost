"""``invoke(tool, args, actor)``: the one way anything in the app gets done.

The UI's buttons, the CLI, the planner's own checks and an MCP client all call it; none of
them has a second path to the state. In order, it:

1. refuses an actor other than ``"human"`` (a person's click) or ``"agent"``, and a call whose
   request ``claimed`` to be anyone but ``actor``: the server takes the actor from the
   session, never from the request, and a request that names another is logged as the
   session's, refused;
2. refuses a human-only verb for any actor but ``"human"``, before anything else runs;
3. refuses a name that is neither an agent tool nor a verb;
4. checks ``args`` against the tool's schema, refusing a missing, mistyped or unknown field;
5. runs the handler, which refuses (``Refused``, or the planner's ``PlanRefused``) before it
   changes anything. A search that fails is an error, not a refusal: the check has already
   recorded it on the case's trace as ``failed``. A check tool called on its own keeps the
   planner's stop rules; ``by_planner`` marks the checks the planner itself makes, which it has
   already checked against those rules.

Every call, refused or not, lands in the activity log with its actor and outcome, and a
nested call (the planner's checks inside ``investigate``) is logged after the call that
started it, marked ``planner``. What comes back is a copy, so no caller can reach into the
state through it.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from offer_checkpost.planner import PlanRefused
from offer_checkpost.providers import SearchError
from offer_checkpost.store import ACTORS, Refused, Store
from offer_checkpost.tools import TOOLS, Call, Handler
from offer_checkpost.verbs import VERBS

# ``claimed`` for a request that named no actor, as every caller but the server's is.
UNCLAIMED: Any = object()
CLAIMED = (
    "who is calling comes from the session, not from the request: a request cannot claim to be "
    "the person. Leave actor out; nothing was run"
)


def invoke(
    tool: str,
    args: Any,
    actor: str,
    *,
    store: Store,
    by_planner: bool = False,
    claimed: Any = UNCLAIMED,
) -> dict[str, Any]:
    """Runs one tool or verb. Returns ``{"ok": True, "result": ...}``, or
    ``{"ok": False, "outcome": "refused" | "error", "error": "<why>"}``; ``error`` is the
    search provider's fixed message when a search failed, never exception text."""
    with store.lock:
        try:
            schema, handler = _route(tool, actor, claimed)
            if problems := validate(schema, args):
                raise Refused(f"{tool} was refused: {'; '.join(problems)}")
        except Refused as refusal:
            store.log(actor, tool, args, "refused", refusal.message, planner=by_planner)
            return {"ok": False, "outcome": "refused", "error": refusal.message}

        entry = store.log(actor, tool, args, "running", planner=by_planner)
        try:
            result = handler(Call(store, actor, by_planner), copy.deepcopy(args))
        except (Refused, PlanRefused) as refusal:
            message = refusal.message if isinstance(refusal, Refused) else refusal.reason
            store.settle(entry, "refused", message)
            return {"ok": False, "outcome": "refused", "error": message}
        except SearchError as failure:
            store.settle(entry, "error", failure.message)
            return {"ok": False, "outcome": "error", "error": failure.message}
        except Exception as failure:
            # The type only: an exception's text can carry a request URL with the key in it.
            store.settle(entry, "error", type(failure).__name__)
            raise
        store.settle(entry, "ok")
        return {"ok": True, "result": copy.deepcopy(result)}


def _route(tool: Any, actor: Any, claimed: Any) -> tuple[dict[str, Any], Handler]:
    if actor not in ACTORS:
        raise Refused("unknown actor: a call comes from 'human' (a person) or 'agent'")
    if claimed is not UNCLAIMED and claimed != actor:
        raise Refused(CLAIMED)
    name = tool if isinstance(tool, str) else None
    if name in VERBS:
        verb = VERBS[name]
        if actor != "human":
            raise Refused(
                f"{name} is human-only: only a person can {verb.does}. It is not an agent "
                "tool, and nothing was changed"
            )
        return verb.schema, verb.handler
    if name in TOOLS:
        return TOOLS[name].schema, TOOLS[name].handler
    shown = f" {name[:64]!r}" if name else ""
    raise Refused(f"unknown tool{shown}: the agent's tools are the ones in its tool list")


# ---- argument schemas ---------------------------------------------------------------------
# The subset of JSON Schema the registry uses: type, enum, required, properties,
# additionalProperties: false, minProperties, minLength, maxLength, pattern, minimum.

_IS = {
    "object": lambda v: isinstance(v, dict),
    "string": lambda v: isinstance(v, str),
    # JSON has no separate booleans-as-numbers; Python's bool is an int subclass.
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}
_NAMED = {
    "object": "an object",
    "string": "text",
    "integer": "a whole number",
    "boolean": "true or false",
    "null": "null",
}


def validate(schema: dict[str, Any], value: Any, where: str = "args") -> list[str]:
    """What is wrong with ``value`` against ``schema``, one line per problem; empty when
    nothing is."""
    types = schema.get("type")
    if types is not None:
        types = [types] if isinstance(types, str) else types
        if not any(_IS[t](value) for t in types):
            return [f"{where} must be {' or '.join(_NAMED[t] for t in types)}"]
    if value is None:
        return []
    if "enum" in schema and value not in schema["enum"]:
        return [f"{where} must be one of {', '.join(map(repr, schema['enum']))}"]
    if isinstance(value, str):
        return _string(schema, value, where)
    if isinstance(value, int) and "minimum" in schema and value < schema["minimum"]:
        return [f"{where} must be at least {schema['minimum']}"]
    if isinstance(value, dict):
        return _object(schema, value, where)
    return []


def _string(schema: dict[str, Any], value: str, where: str) -> list[str]:
    if len(value) < schema.get("minLength", 0):
        return [f"{where} must not be empty" if not value else f"{where} is too short"]
    if "maxLength" in schema and len(value) > schema["maxLength"]:
        return [f"{where} is longer than {schema['maxLength']} characters"]
    if "pattern" in schema and not re.search(schema["pattern"], value):
        example = schema.get("description", "")
        return [f"{where} is not in the expected form" + (f" ({example})" if example else "")]
    return []


def _object(schema: dict[str, Any], value: dict[str, Any], where: str) -> list[str]:
    properties = schema.get("properties", {})
    problems = []
    if schema.get("additionalProperties") is False:
        unknown = sorted(str(k) for k in value if k not in properties)
        if unknown:
            allowed = ", ".join(properties) or "no fields"
            problems.append(
                f"{where} has unknown field{'s' if len(unknown) > 1 else ''} "
                f"{', '.join(unknown)} (allowed: {allowed})"
            )
    missing = [k for k in schema.get("required", []) if k not in value]
    if missing:
        problems.append(f"{where} is missing {', '.join(missing)}")
    if len(value) < schema.get("minProperties", 0):
        problems.append(f"{where} must name at least {schema['minProperties']} field")
    for key, sub in properties.items():
        if key in value:
            problems += validate(sub, value[key], f"{where}.{key}")
    return problems
