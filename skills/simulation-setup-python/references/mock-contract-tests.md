# Generate test_mock_contract.py

Read this when generating `plurai_sim/test_mock_contract.py` — the acceptance
criteria for the backend's mock environment.

> **Scope.** `test_mock_contract.py` validates the mock backend's HTTP contract only. It does **not** import the agent, exercise the orchestrator harness, the storage stub, or `invoke_agent` / `seed_state` — those surface only in the Step 10 smoke run. Passing these contract tests is necessary but not sufficient for a working simulation.

This file is the **acceptance criteria for the backend's mock environment**.

The plugin sees the agent code. The backend does NOT. The plugin extracts what each tool
must do — inputs, outputs, state transitions, constraints, error cases — and encodes it
as executable tests. The backend builds the mock environment, then runs these tests
to verify its implementation is correct.

**These tests do NOT import the agent code.** They only call the mock service HTTP API.
They define the CONTRACT the mock environment must satisfy.

> **Expected values come from the producing line.** The PRD and config say **what** to test. The
> expected **value** comes from the line of code that produces it, and a test that asserts a
> behaviour (a diagnostic text, which rows a filter or select returns, a value's format, a computed
> value, a state transition) ends its docstring with `(source: <path>:<LINE>[-<END>])` naming that
> line. Follow the call to where the value is made; when that is a library call, read the library
> source on the agent's interpreter, where a default argument is behaviour. A docstring or argument
> description is a declaration, and the code wins where they differ. When an external service with
> no source on disk makes the value, this rule adds nothing. Values the test sent or seeded, echoed
> back, need no line.

> **Retrieval / LLM-backed tools — assert determinism AND no hallucination (mandatory).** For any
> tool that RETRIEVES by meaning (semantic/vector search, RAG, NL retrieval, ranking) — the category
> the backend mocks with its own `llm_backed` strategy, which it chooses itself and which is not a
> descriptor key you can set — the
> shape-only checks are not enough — the real fidelity risk is *wrong/unstable data*. Add, per such
> tool: (1) a **determinism** test — call it twice with identical args against the same seeded session
> and assert the returned record SET (and any `score`/order) is identical; (2) an **anti-hallucination**
> test — seed a small known world, call the tool, and assert every returned identifier (the
> `returns.fields` id/name) is present in the seeded world (the mock must never invent an entity the
> agent could then "retrieve"). These pin the two properties a shape test silently misses.

## The five properties that make this tier evidence

A verification tier that cannot fail is coverage theater. These are the ways it happens: every
test errors at fixture setup and the tier reads as silence; every call against a deployed mock
answers 401; the assertions run on the mock's raw HTTP response and never on the envelope the
agent reads, so a harness defect stays invisible even when green; and the assertions pass
identically on the broken and the repaired message. Every generated file must therefore satisfy
all five:

1. **Authenticated.** The deployed mock is behind the Plurai backend's bearer proxy. No
   token means 401 on every call, and a 401 body read as a tool response makes each test a
   verdict about a call the mock never received.
2. **Reachable, or loudly not.** Fixture setup that raises reports ERRORS, and an all-errored
   tier is indistinguishable from a clean one in any summary a caller reads. The preflight and
   the `pytest_sessionfinish` gate below turn both into one non-zero verdict.
3. **Asserted at the level the AGENT reads.** Raw-response assertions test the mock in
   isolation; the agent is handed a ToolMessage built by the dispatch wrapper, and that is
   where the defect class lives. That lives in its own LOCAL file — see
   `plurai_sim/test_agent_envelope.py` below — because it grades the harness, not the mock,
   and the mock-contract file is the only one that ships to the backend.
4. **Drift-sensitive.** Assert the mock's actual words — a distinctive substring of the real
   diagnostic — never `"error" in response`. An assertion that passes on both the broken and
   the repaired message is not measuring the message. Match it against `response_text(...)`,
   never `json.dumps(...)`, which escapes quotes and non-ASCII text.
5. **Structurally faithful.** The mock returns what the agent's own guards read, not only the
   model-visible text: for each `guards[]` entry with `reads: result` over a mocked tool, one test
   calls that tool and asserts every `fields` entry (a status, an artifact list, message metadata) in
   its return (`guard_field_untested`); `test_agent_envelope.py` asserts the harness places it where
   the guard reads it.

Write to `plurai_sim/test_mock_contract.py`:

```python
# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""
Mock Environment Contract Tests
================================
Generated from agent code analysis by Plurai plugin.

These tests define the expected behavior of the mock environment.
The backend implements the mocking environment without seeing the agent code.
These tests verify the implementation is correct.

Run: pytest plurai_sim/test_mock_contract.py -v

The tests call the mock service HTTP API directly.
They do NOT import or reference the agent code.
"""
import json
import os
import warnings

import pytest
import requests

# ================================================================
# Configuration — point to mock service
# ================================================================
# PLURAI_MOCK_URL is injected by the backend's env-gen verify phase
# (the deployed mock listens on a free port allocated at deploy
# time, NOT 8199). Tests MUST read from the env var or they
# connection-refuse every time the deployer picks a port other
# than 8199 — which is always. The localhost:8199 fallback covers
# the in-process LocalMockService default for hand-run tests.

# `or`, not a get-default: `PLURAI_MOCK_URL=""` (what `$(cat missing-file)` produces) is
# SET, so a default argument would not apply and every request would go to "/health".
# `.rstrip("/")`: the deployed URL is `<base>/mock/<env_id>/` WITH a trailing slash, and
# `f"{URL}{path}"` would then build `.../mock/<id>//health`. Nothing in requests, urllib3 or
# Starlette collapses a double slash, so the proxy forwards `//health` and the mock 404s —
# which the preflight below would report as an unhealthy mock, against a healthy one.
MOCK_SERVICE_URL = (os.environ.get("PLURAI_MOCK_URL") or "http://localhost:8199").rstrip("/")

# The DEPLOYED mock is reached through the Plurai backend's `/mock/<env_id>/`
# reverse proxy, which sits behind the bearer middleware. Unauthenticated calls
# are answered 401 by the proxy — the tool is never dispatched, so every
# assertion below would be grading an auth error. Empty for the in-process
# LocalMockService, which has no proxy and needs no token.
MOCK_AUTH_TOKEN = os.environ.get("PLURAI_TOKEN", "")
_AUTH_HEADERS = {"Authorization": f"Bearer {MOCK_AUTH_TOKEN}"} if MOCK_AUTH_TOKEN else {}


def _json_or_raise(resp, what: str) -> dict:
    """The response BODY, or a raise carrying the status.

    `resp.json()` on a 401 / 404 / 502 returns the proxy's error object, and
    `result.get("response", result)` then hands it on as though the tool had
    answered. Assertions written for a tool response run against a transport
    error and report on the mock's behaviour — a verdict about a call the mock
    never received. The status has to become an exception here or it becomes a
    contract finding downstream.
    """
    if resp.status_code >= 400:
        hint = ""
        if resp.status_code == 401:
            hint = (
                " — the deployed mock sits behind the Plurai backend's bearer proxy; "
                "export PLURAI_TOKEN before running this tier"
            )
        raise AssertionError(f"{what} -> HTTP {resp.status_code}: {resp.text[:300]}{hint}")
    return resp.json()


def api_post(path: str, body: dict) -> dict:
    resp = requests.post(f"{MOCK_SERVICE_URL}{path}", json=body, headers=_AUTH_HEADERS, timeout=30)
    return _json_or_raise(resp, f"POST {path}")


def api_get(path: str) -> dict:
    resp = requests.get(f"{MOCK_SERVICE_URL}{path}", headers=_AUTH_HEADERS, timeout=10)
    return _json_or_raise(resp, f"GET {path}")


# ================================================================
# Preflight — a tier that cannot reach the mock must say so ONCE
# ================================================================
# Every fixture below starts with `/session/create`. When the service is
# unreachable or the token is missing, that raises inside FIXTURE SETUP, and
# pytest reports the whole tier as ERRORS: not failures, not skips. A caller
# grepping for "failed" reads 13 errored tests as silence and ships. Live, this
# tier reported zero effective coverage for exactly this reason.
#
# The preflight turns that into ONE actionable line before any test runs.


@pytest.fixture(scope="session", autouse=True)
def _preflight():
    # `pytest.fail`, NOT `pytest.exit`. This conftest lands in the customer's repo, and
    # `pytest.exit` from a session fixture aborts their ENTIRE suite — this tier is
    # documented as optional locally and must not be able to do that. Failing here errors
    # this module's tests once, with the actionable line, and the conftest gate below turns
    # that into a non-zero verdict instead of the silence it used to be.
    try:
        resp = requests.get(f"{MOCK_SERVICE_URL}/health", headers=_AUTH_HEADERS, timeout=10)
    except requests.RequestException as exc:
        pytest.fail(
            f"mock contract tier: {MOCK_SERVICE_URL} is unreachable ({exc}). "
            f"Set PLURAI_MOCK_URL to the deployed mock's URL "
            f"(the localhost fallback is only for an in-process LocalMockService).",
            pytrace=False,
        )
    if resp.status_code == 401:
        pytest.fail(
            f"mock contract tier: {MOCK_SERVICE_URL} answered 401. The deployed mock is behind "
            f"the Plurai backend's bearer proxy — export PLURAI_TOKEN "
            f"({'a token IS set but was rejected' if MOCK_AUTH_TOKEN else 'no token is set'}).",
            pytrace=False,
        )
    if resp.status_code >= 400:
        pytest.fail(
            f"mock contract tier: {MOCK_SERVICE_URL}/health answered HTTP {resp.status_code}: "
            f"{resp.text[:300]}",
            pytrace=False,
        )


# The "did this tier verify anything?" gate lives in plurai_sim/conftest.py — see
# below. It CANNOT live in this file: pytest registers hooks from conftest.py and
# installed plugins only, so a `pytest_sessionfinish` defined in a test module is
# never called and the gate would be dead code that reads correctly in review.


# ================================================================
# Fixtures
# ================================================================

# The seeded world. Key every row exactly as the backend keys a scenario world: by the string of
# its own id — its type's `world_state.entity_types.<type>.id_field` value, else its `id`, else
# `<type>_id` (`<type>_<n>` only for a type with none). Never a handle (`card_1` for `card_id: 101`):
# the mock resolves an id through `get_entity(<id>, '<type>')`,
# which finds a row by its key, `id` or `<type>_id`, so a handle-keyed fixture fails a handler that
# is right for every real scenario and passes one that is not. A `references:` field holds the
# target's key, and a test that reads a row back uses that key. Keep ids distinct across types (the
# seeder suffixes a shared one). `verify_config` refuses any other key (`world_fixture_key_not_id`).
WORLD = {
    # ---- {Entity: user} ----
    "user_123": {
        "_entity_type": "user",
        "user_id": "user_123",
        "id": "user_123",
        "name": "Alice Johnson",
        "email": "alice@example.com",
        "plan": "premium",         # enum: [free, pro, premium, enterprise]
        "balance": 347.00,         # float, min: 0.0
        "status": "active",        # enum: [active, suspended, deleted]
    },
    # ---- {Entity: order} ----
    "order_456": {
        "_entity_type": "order",
        "order_id": "order_456",
        "id": "order_456",
        "user_id": "user_123",     # references: user
        "status": "delivered",     # enum: [pending, paid, shipped, delivered, cancelled, refunded]
        "total": 89.99,            # float, min: 0.01
        "items": [
            {"product_id": "prod_A", "name": "Widget A", "qty": 1, "price": 49.99},
            {"product_id": "prod_B", "name": "Widget B", "qty": 1, "price": 40.00},
        ],
        "delivered_at": "2024-01-10T14:00:00Z",
    },
    # ---- {Entity: transaction} ----
    "txn_001": {
        "_entity_type": "transaction",
        "transaction_id": "txn_001",
        "id": "txn_001",
        "user_id": "user_123",
        "order_id": "order_456",
        "amount": 89.99,
        "type": "charge",          # enum: [charge, refund, subscription]
        "status": "completed",     # enum: [completed, pending, failed]
    },
    # {Add all entity types from simulation_prd.md Section 2}
}


@pytest.fixture
def session():
    """Create a fresh session seeded with WORLD."""
    import uuid
    sid = f"contract-test-{uuid.uuid4().hex[:8]}"

    initial_state = {"entities": json.loads(json.dumps(WORLD))}

    agent_context = {"user_id": "user_123", "permissions": ["refund", "view_account", "view_orders"]}
    api_post("/session/create", {
        "session_id": sid,
        "initial_state": initial_state,
        "agent_context": agent_context,
    })
    yield sid


@pytest.fixture
def empty_session():
    """Session with no entities — for testing not-found cases."""
    import uuid
    sid = f"contract-test-empty-{uuid.uuid4().hex[:8]}"
    api_post("/session/create", {
        "session_id": sid,
        "initial_state": {"entities": {}},
        "agent_context": {"user_id": "user_nonexistent"},
    })
    yield sid


# {tool_name: descriptor.returns.type} for every mocked tool, from plurai.config.yaml.
_RETURN_TYPES: dict[str, str] = {}
_JSON_TYPES = {"str": str, "list": list, "dict": dict, "int": int, "float": (int, float), "bool": bool, "null": type(None), "None": type(None)}


def _split(text: str, sep: str) -> list[str]:
    """`text` split on `sep` outside brackets: `Union[dict[str, int], None]` has two members."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        if ch == sep and depth == 0:
            parts.append(cur)
            cur = ""
            continue
        depth += (ch == "[") - (ch == "]")
        cur += ch
    return [p.strip() for p in parts + [cur] if p.strip()]


def _is_json(content, kind) -> bool:
    if not isinstance(content, str):
        return False
    try:
        decoded = json.loads(content)
    except ValueError:
        return False
    return kind is None or isinstance(decoded, kind)


def _content_check(declared: str):
    """`returns.type` as a predicate on `content` (a union checks membership), or None when it cannot
    be checked: `unknown`, or a member outside the vocabulary."""
    for wrapper, extra in (("Optional[", ["null"]), ("Union[", [])):
        if declared.startswith(wrapper) and declared.endswith("]"):
            members = _split(declared[len(wrapper):-1], ",") + extra
            break
    else:
        members = _split(declared, "|")
    checks = []
    for member in members:
        if member == "json" or member.startswith("json["):
            kind = {"list": list, "dict": dict}.get(member[5:-1].split("[")[0]) if member != "json" else None
            checks.append(lambda c, kind=kind: _is_json(c, kind))
        elif _JSON_TYPES.get(member.split("[")[0].strip()) is not None:
            checks.append(lambda c, want=_JSON_TYPES[member.split("[")[0].strip()]: isinstance(c, want))
        else:
            return None
    return (lambda c: any(check(c) for check in checks)) if checks else None


@pytest.fixture(scope="session", autouse=True)
def _unchecked_return_types():
    unchecked = sorted(tool for tool, declared in _RETURN_TYPES.items() if _content_check(declared) is None)
    if unchecked:
        warnings.warn(f"returns.type is not checked for {len(unchecked)} tool(s) (unknown or outside the vocabulary): {', '.join(unchecked)}")


def execute_tool(sid: str, tool: str, args: dict) -> dict:
    """Call a mock tool and return the RAW mock response.

    This is the mock's half of the contract. It is NOT what the agent reads — that
    is `plurai_sim/test_agent_envelope.py`, which grades the HARNESS's rendering.
    A success's `content` is the only key the agent's model sees, so every call checks it
    is there, in the declared `returns.type`: `json[...]` is a str that decodes to that kind,
    and a union accepts any of its members.
    """
    result = api_post("/mock/execute", {
        "session_id": sid, "tool": tool, "args": args, "turn": 0,
    })
    response = result.get("response", result)
    if isinstance(response, dict) and response.get("status") == "success":
        assert "content" in response, f"{tool}: success has no `content` (keys {sorted(response)}) — only `content` reaches the model"
        declared = _RETURN_TYPES.get(tool, "")
        check = _content_check(declared)
        assert check is None or check(response["content"]), (
            f"{tool}: content is {type(response['content']).__name__}, the descriptor's returns.type is {declared}"
        )
    return response


def response_text(response) -> str:
    """Every string in a mock response, decoded: what a diagnostic substring is matched against.

    Not `json.dumps(response)`: that escapes `"` and `\\` and, by default, every non-ASCII
    character, so a quoted or non-English diagnostic can never match it. A string that is
    itself JSON (a `json[...]` content) is walked too.
    """
    if isinstance(response, dict):
        return "\n".join(response_text(v) for v in response.values())
    if isinstance(response, (list, tuple)):
        return "\n".join(response_text(v) for v in response)
    if not isinstance(response, str):
        return "" if response is None else str(response)
    try:
        decoded = json.loads(response)
    except ValueError:
        return response
    return response + "\n" + response_text(decoded) if isinstance(decoded, (dict, list)) else response


def get_entity(sid: str, entity_id: str) -> dict:
    """Read an entity from WorldState."""
    result = api_get(f"/state/{sid}/{entity_id}")
    return result.get("entity", {})


def get_all_entities(sid: str, allow_empty: bool = False) -> dict:
    """Every entity in WorldState — or a raise.

    Every constraint, foreign-key, balance and versioned-entity test below is a LOOP over
    what this returns, and a loop over nothing PASSES. Two absences therefore raise here
    rather than being handed on as `{}`:

    * a response carrying no `state` key — one endpoint-shape drift and `.get("state", {})`
      turns the whole tier green over a world it never read, including one holding
      `status="BOGUS_NOT_IN_ENUM"`;
    * an empty world where the fixture seeds one — the seed did not materialise, which is a
      finding, not a clean run.

    `allow_empty=True` is for the test whose SUBJECT is emptiness (the `empty_session`
    fixture). It has to be asked for, so that no test acquires it by accident.
    """
    result = api_get(f"/state/{sid}")
    if "state" not in result:
        raise AssertionError(
            f"GET /state/{sid} returned no 'state' key (got {sorted(result)[:5]}) — "
            f"every constraint test would iterate an empty world and pass"
        )
    state = result["state"]
    if not state and not allow_empty:
        raise AssertionError(
            f"GET /state/{sid} returned an EMPTY world — the fixture's seed did not "
            f"materialise, so every constraint test below would pass over nothing"
        )
    return state


# ================================================================
# {tool_name}: READ TOOL CONTRACT
# ================================================================
# The agent calls this tool to read {entity_type} data.
# The agent accesses these fields from the response:
#   {list fields the agent reads, from Step 6 analysis}

class Test_{ReadToolName}:
    """Contract: {tool_name} must return {entity_type} with correct shape."""

    def test_returns_entity_with_required_fields(self, session):
        """
        GIVEN: user_123 exists in WorldState
        WHEN: {tool_name} is called with user_id="user_123"
        THEN: response contains all fields the agent accesses
        """
        content = execute_tool(session, "{tool_name}", {"user_id": "user_123"})["content"]

        # --- Fields the agent accesses (from code analysis) ---
        assert "name" in content, "Agent reads result['name']"
        assert "plan" in content, "Agent reads result['plan']"
        assert "balance" in content, "Agent reads result['balance']"

        # --- Types must match agent expectations ---
        assert isinstance(content["name"], str)
        assert isinstance(content["plan"], str)
        assert isinstance(content["balance"], (int, float))

        # --- Values must come from WorldState ---
        assert content["name"] == "Alice Johnson"
        assert content["plan"] == "premium"
        assert content["balance"] == pytest.approx(347.00)

    def test_not_found_returns_error(self, session):
        """
        GIVEN: user_999 does NOT exist
        WHEN: {tool_name} is called with user_id="user_999"
        THEN: response contains error (not crash, not empty)

        The agent checks for error like this:
          if "error" in result: tell_user("not found")

        (source: {not_found_path}:{not_found_line})
        """
        response = execute_tool(session, "{tool_name}", {"user_id": "user_999"})
        assert "error" in response
        # DRIFT-SENSITIVE. `"error" in response` alone passes on any error text at
        # all, including one that names nothing the agent can act on. Name a
        # distinctive phrase from the not-found message the producing line words.
        assert "{not_found_substring}" in response_text(response)

    def test_returns_correct_entity_not_random(self, session):
        """
        GIVEN: multiple users in WorldState
        WHEN: {tool_name} is called for a specific user
        THEN: returns THAT user, not a random one
        """
        content = execute_tool(session, "{tool_name}", {"user_id": "user_123"})["content"]
        # Must be user_123's data specifically, not any user
        assert content.get("email") == "alice@example.com"


# ================================================================
# {tool_name}: LIST TOOL CONTRACT
# ================================================================
# The agent calls this tool to list {entity_type}s filtered by {filter}.
# The agent iterates the result: for item in result: ...

class Test_{ListToolName}:
    """Contract: {tool_name} must return filtered list."""

    def test_returns_list(self, session):
        """
        GIVEN: orders exist for user_123
        WHEN: {tool_name}(user_id="user_123")
        THEN: returns a list (not dict, not None)
        """
        response = execute_tool(session, "{tool_name}", {"user_id": "user_123"})["content"]
        assert isinstance(response, list), f"Expected list, got {type(response)}"

    def test_filters_correctly(self, session):
        """
        GIVEN: orders for user_123 exist, orders for user_999 do not
        WHEN: {tool_name}(user_id="user_123")
        THEN: returns only user_123's orders
        """
        response = execute_tool(session, "{tool_name}", {"user_id": "user_123"})["content"]
        assert len(response) >= 1
        for item in response:
            assert item.get("user_id") == "user_123"

    def test_no_results_returns_empty_list(self, empty_session):
        """
        GIVEN: no orders exist
        WHEN: {tool_name}(user_id="user_nonexistent")
        THEN: returns [] (empty list, NOT error, NOT None)

        The agent does: if len(result) == 0: tell_user("no orders found")
        """
        response = execute_tool(empty_session, "{tool_name}", {"user_id": "user_nonexistent"})["content"]
        assert isinstance(response, list)
        assert len(response) == 0

    def test_list_items_have_required_fields(self, session):
        """Each item in the list must have the fields the agent accesses."""
        response = execute_tool(session, "{tool_name}", {"user_id": "user_123"})["content"]
        for item in response:
            # {List every field the agent reads from each list item}
            assert "order_id" in item or "id" in item
            assert "status" in item
            assert "total" in item


# ================================================================
# {tool_name}: WRITE TOOL CONTRACT
# ================================================================
# The agent calls this to {what it does}.
# Business rules from simulation_prd.md; each expected value from its producing line:
#   1. {rule 1}
#   2. {rule 2}

class Test_{WriteToolName}:
    """Contract: {tool_name} must modify WorldState correctly."""

    def test_success_response_shape(self, session):
        """
        GIVEN: valid preconditions (order exists, status=delivered)
        WHEN: {tool_name}({valid_args})
        THEN: response has {expected_fields}

        The agent reads: result["status"], result["{field}"]
        """
        response = execute_tool(session, "{tool_name}", {
            # {valid args for this tool}
            "order_id": "order_456",
            "reason": "damaged item",
        })
        # Agent checks these fields:
        assert "status" in response
        assert response["status"] != "error"
        # {assert other fields the agent reads}

    def test_mutates_target_entity(self, session):
        """
        GIVEN: order_456 status is "delivered"
        WHEN: {tool_name}(order_id="order_456", ...)
        THEN: order_456.status becomes "{new_status}"

        This is critical: downstream tools read this entity.
        If the mutation doesn't happen, the tool chain breaks.
        """
        execute_tool(session, "{tool_name}", {
            "order_id": "order_456",
            "reason": "damaged item",
        })
        order = get_entity(session, "order_456")
        assert order["status"] == "refunded", \
            f"Expected status 'refunded', got '{order.get('status')}'"

    def test_updates_related_entity(self, session):
        """
        GIVEN: user_123 balance is 347.00, order total is 89.99
        WHEN: {tool_name}(order_id="order_456")
        THEN: user_123.balance increases by 89.99 → 436.99

        The agent later calls get_user_profile and reads the new balance.
        """
        execute_tool(session, "{tool_name}", {
            "order_id": "order_456",
            "reason": "damaged item",
        })
        user = get_entity(session, "user_123")
        assert user["balance"] == pytest.approx(436.99), \
            f"Expected balance 436.99, got {user.get('balance')}"

    def test_creates_new_entity(self, session):
        """
        WHEN: {tool_name} succeeds
        THEN: a new {entity_type} entity is created in WorldState

        The agent may later call {downstream_tool} which reads this entity.
        """
        response = execute_tool(session, "{tool_name}", {
            "order_id": "order_456",
            "reason": "damaged item",
        })
        # Get the new entity ID from the response
        new_id = response["content"].get("refund_id")
        assert new_id is not None, "Response must include the new entity ID"

        # Verify it exists in WorldState
        new_entity = get_entity(session, new_id)
        assert new_entity.get("_entity_type") == "refund"
        assert new_entity.get("amount") == pytest.approx(89.99)
        assert new_entity.get("order_id") == "order_456"

    def test_rejects_nonexistent_target(self, session):
        """
        GIVEN: order_999 does NOT exist
        WHEN: {tool_name}(order_id="order_999")
        THEN: returns error, WorldState unchanged

        The agent checks: if "error" in result: tell_user(result["error"])
        """
        response = execute_tool(session, "{tool_name}", {
            "order_id": "order_999",
            "reason": "test",
        })
        assert "error" in response
        assert "{not_found_substring}" in response_text(response)
        # WorldState must be unchanged
        user = get_entity(session, "user_123")
        assert user["balance"] == pytest.approx(347.00), "Balance must not change on error"

    # --- Business rule tests ---
    # {One test per business rule in simulation_prd.md Section 5, its expected value
    #  and `(source: …)` from the producing line}

    def test_rule_refund_window(self, session):
        """
        RULE: "Refund window is 30 days from delivery date"
        GIVEN: order delivered 60 days ago
        WHEN: {tool_name}(order_id=...)
        THEN: returns error with "refund_window_expired"
        """
        # {Modify WorldState to set up the precondition}
        # e.g., update order_456.delivered_at to 60 days ago
        # Then call the tool and assert error
        pass  # {FILL IN — backend must implement this rule}

    def test_rule_already_refunded(self, session):
        """
        RULE: "Cannot refund an already-refunded order"
        GIVEN: order_456.status == "refunded"
        WHEN: {tool_name}(order_id="order_456")
        THEN: returns error with "already_refunded"
        """
        # First refund — should succeed
        execute_tool(session, "{tool_name}", {
            "order_id": "order_456", "reason": "first",
        })
        # Second refund — should fail
        response = execute_tool(session, "{tool_name}", {
            "order_id": "order_456", "reason": "second",
        })
        assert "error" in response
        # The RULE has to be named, not merely violated: an error saying only that
        # something went wrong is indistinguishable from the mock being broken, both
        # to the agent and to whoever reads this test's verdict.
        assert "{rule_violation_substring}" in response_text(response)

    def test_rule_balance_never_negative(self, session):
        """
        RULE: "User balance can never go below zero"
        GIVEN: any operation
        THEN: user.balance >= 0 always
        """
        # {Execute tool that modifies balance}
        execute_tool(session, "{tool_name}", {
            "order_id": "order_456", "reason": "test",
        })
        entities = get_all_entities(session)
        for eid, entity in entities.items():
            if entity.get("_entity_type") == "user":
                assert entity.get("balance", 0) >= 0, \
                    f"User {eid} has negative balance: {entity.get('balance')}"


# ================================================================
# TOOL CHAIN CONTRACTS
# ================================================================
# These test that connected tools share state correctly.
# From the tool_graph in plurai.config.yaml:
#   {tool_a} --writes--> [{entity}] <--reads-- {tool_b}

class TestToolChains:
    """Contract: tool writes must be visible to downstream tool reads."""

    def test_{tool_a}_then_{tool_b}(self, session):
        """
        CHAIN: {tool_a} writes {entity}, {tool_b} reads {entity}
        GIVEN: initial state
        WHEN: {tool_a}({args}) then {tool_b}({args})
        THEN: {tool_b} sees {tool_a}'s changes
        """
        # Step 1: tool_a writes
        execute_tool(session, "{tool_a}", {
            # {tool_a args}
        })

        # Step 2: tool_b reads — must see the change
        response = execute_tool(session, "{tool_b}", {
            # {tool_b args}
        })
        # {Assert tool_b sees tool_a's mutation}
        # Example: after process_refund, get_transactions should include
        # a new transaction with type="refund"

    def test_multiple_writes_accumulate(self, session):
        """
        Multiple writes should all be visible, not just the last one.
        """
        # {Write with tool_a multiple times}
        # {Read with tool_b — should see ALL writes}
        pass


# ================================================================
# ENUM / CONSTRAINT CONTRACTS
# ================================================================
# Which constraints: simulation_prd.md Section 2 (Entity Schemas); each allowed
# value: the code that defines it

class TestConstraints:
    """Contract: mock environment must respect schema constraints."""

    def test_order_status_enum(self, session):
        """Order status must be one of the valid enum values."""
        VALID = {"pending", "paid", "shipped", "delivered", "cancelled", "refunded"}
        entities = get_all_entities(session)
        for eid, entity in entities.items():
            if entity.get("_entity_type") == "order":
                assert entity.get("status") in VALID, \
                    f"Order {eid} has invalid status: {entity.get('status')}"

    def test_transaction_type_enum(self, session):
        """Transaction type must be one of the valid enum values."""
        VALID = {"charge", "refund", "subscription"}
        entities = get_all_entities(session)
        for eid, entity in entities.items():
            if entity.get("_entity_type") == "transaction":
                assert entity.get("type") in VALID, \
                    f"Transaction {eid} has invalid type: {entity.get('type')}"

    def test_referential_integrity(self, session):
        """Foreign key references must point to existing entities."""
        entities = get_all_entities(session)
        for eid, entity in entities.items():
            if "user_id" in entity and entity["user_id"]:
                assert entity["user_id"] in entities, \
                    f"Entity {eid} references nonexistent user {entity['user_id']}"
            if "order_id" in entity and entity["order_id"]:
                assert entity["order_id"] in entities, \
                    f"Entity {eid} references nonexistent order {entity['order_id']}"

    # {Add constraint tests for every enum, min/max, required field
    #  from simulation_prd.md Section 2}


# ================================================================
# STATE MACHINE CONTRACTS
# ================================================================
# Which state machines: simulation_prd.md Section 2; each transition's outcome:
# the line that makes it

class TestStateMachines:
    """Contract: state transitions must follow defined state machines."""

    def test_order_valid_transitions(self, session):
        """
        Order state machine:
          pending → paid → shipped → delivered
          pending → cancelled
          delivered → refunded (within 30 days)
          shipped → returned → refunded

        Invalid: delivered → pending (backwards)
        Invalid: cancelled → shipped (dead state)
        """
        # {Test a valid transition}
        # {Test that an invalid transition is rejected or doesn't happen}
        pass

    # {One test per state machine from the PRD}


# ================================================================
# VERSIONED-ENTITY CONTRACTS
# ================================================================
# ONLY when the schema has versioned / HEAD-tracked entities
# (revisions, branches, document versions). Every test above passes
# on a mock that stores ONE record entity per row_id shared across
# revisions — while history, revert and diff are all silently dead,
# because nothing else here ever reads a PAST revision.
#
# The `session` fixture must SEED one: a {revision_entity_type} plus
# its row entities, keyed exactly the way the seam mock writes them
# (rule 4's snapshot key, below the template). The seed is what the first two
# tests below grade — every later test writes a revision of its own
# and would pass on top of a seed nothing can read.

class TestVersionedEntities:
    """Contract: each revision owns a SNAPSHOT of its rows.

    Placeholders beyond the tool names: {revision_entity_type} is the version entity's
    `_entity_type` (`revision` / `version` / `commit` / `draft` — whatever the schema calls
    it), {parent_id_field} is the field on it naming its {parent_entity} (`collection_id`,
    `document_id`), {record_entity_type} / {label_field} are the row entity's type and the
    field a per-label precondition counts, {diff_changed_key} is the key {diff_tool} returns
    its per-row changes under. Substitute all of them — a hardcoded guess reads as a passing
    test on a schema that spells them differently.
    """

    def test_seeded_record_ids_resolve(self, session):
        """
        GIVEN: the fixture seeds a {revision_entity_type} with its rows
        WHEN:  each id in the seeded revision's `record_ids` is looked up in WorldState
        THEN:  every one of them names an entity that exists
        WHY:   `record_ids` holds STORE KEYS. Seeded with bare {row_id}s — or with any id the
               world is not keyed on — every walk resolves to nothing while the rows are
               plainly present, so a scenario precondition that counts rows reads zero and its
               deterministic check fails against a world that is entirely correct. Nothing
               errors: an empty walk is a legal answer. This is the ONLY test that reads the
               seed's own index, which is why the class of defect reached a scored run.
        """
        entities = get_all_entities(session)
        revisions = {eid: e for eid, e in entities.items()
                     if e.get("_entity_type") == "{revision_entity_type}"}
        assert revisions, "the fixture seeds no {revision_entity_type}"
        for rev_id, rev in revisions.items():
            record_ids = rev.get("record_ids") or []
            assert record_ids or rev.get("records"), f"{rev_id}: no rows in either shape"
            unresolved = [rid for rid in record_ids if rid not in entities]
            assert not unresolved, (
                f"{rev_id}: record_ids do not name entities — {unresolved[:3]}. "
                f"They are the row ids, not the snapshot keys the world is keyed on."
            )

    def test_a_per_label_precondition_resolves_against_the_seed(self, session):
        """
        GIVEN: the seeded revision, whose rows carry {label_field}
        WHEN:  a scenario precondition counts rows per {label_field} by walking record_ids
               (the min-per-label shape the backend compiles deterministic checks into)
        THEN:  it reaches the rows and counts them
        WHY:   resolution alone is not enough — the rows have to arrive with the field the
               precondition reads. A snapshot that keeps only the key and drops {label_field}
               resolves perfectly and still counts zero of everything.
        """
        entities = get_all_entities(session)
        head = next((e for e in entities.values()
                     if e.get("_entity_type") == "{revision_entity_type}" and e.get("is_head")), None)
        assert head, "no seeded {revision_entity_type} claims HEAD"
        rows = [entities[rid] for rid in (head.get("record_ids") or [])] or head.get("records") or []
        assert rows, "the HEAD revision reaches no rows"
        per_label = {}
        for row in rows:
            per_label[row.get("{label_field}")] = per_label.get(row.get("{label_field}"), 0) + 1
        assert None not in per_label, (
            "rows reached but {label_field} is absent on them: " f"{per_label}"
        )

    def test_revert_restores_prior_content(self, session):
        """
        GIVEN: revision 1 labels {row_id} "{label_a}"; revision 2 relabels it "{label_b}"
        WHEN:  {revert_tool} back to revision 1
        THEN:  the rows read back "{label_a}" — the CONTENT, not just a moved HEAD pointer
        WHY:   a revert that only re-points HEAD is a no-op the agent reports as success.
        """
        execute_tool(session, "{create_revision_tool}", {"rows": [{"id": "{row_id}", "label": "{label_b}"}]})
        execute_tool(session, "{revert_tool}", {"to_index": 1})
        rows = execute_tool(session, "{get_revision_tool}", {})["content"]["rows"]
        assert [r["label"] for r in rows if r["id"] == "{row_id}"] == ["{label_a}"]

    def test_a_past_revision_is_immutable_after_later_writes(self, session):
        """
        GIVEN: revision 1 exists
        WHEN:  revision 2 rewrites the same row
        THEN:  reading revision 1 BY INDEX still returns its own values
        WHY:   one entity per row_id makes every historical read resolve to CURRENT values.
        """
        before = execute_tool(session, "{get_revision_tool}", {"index": 1})["content"]["rows"]
        execute_tool(session, "{create_revision_tool}", {"rows": [{"id": "{row_id}", "label": "{label_b}"}]})
        assert execute_tool(session, "{get_revision_tool}", {"index": 1})["content"]["rows"] == before

    def test_exactly_one_head(self, session):
        """
        THEN: exactly ONE revision PER {parent_entity} carries is_head truthy.
        WHY:  two claimants (or none) make "the current revision" whatever the read path
              happens to fall back to, and every downstream judge sees a different answer.
              Grouped by parent: a fixture seeding two {parent_entity}s has two legitimate
              HEADs, and a global count fails a correct mock.
        """
        execute_tool(session, "{create_revision_tool}", {"rows": [{"id": "{row_id}", "label": "{label_b}"}]})
        heads_by_parent = {}
        for e in get_all_entities(session).values():
            if e.get("_entity_type") == "{revision_entity_type}" and e.get("is_head"):
                heads_by_parent.setdefault(e.get("{parent_id_field}"), []).append(e)
        assert heads_by_parent, "no revision claims HEAD"
        for parent_id, heads in heads_by_parent.items():
            assert len(heads) == 1, f"{parent_id}: expected one HEAD, found {len(heads)}"

    def test_a_label_change_produces_a_nonempty_diff(self, session):
        """
        GIVEN: revision 1 labels {row_id} "{label_a}"
        WHEN:  revision 2 relabels it "{label_b}" and {diff_tool} compares 1 → 2
        THEN:  the diff NAMES {row_id} — a diff that is ALWAYS empty is not a passing diff,
               and a truthy-but-unrelated payload is not either.
        WHY:   with snapshots shared across revisions both sides read identically, so the
               diff is structurally empty and every "no changes" answer looks correct.
        """
        execute_tool(session, "{create_revision_tool}", {"rows": [{"id": "{row_id}", "label": "{label_b}"}]})
        diff = execute_tool(session, "{diff_tool}", {"from_index": 1, "to_index": 2})["content"]
        changed = diff.get("{diff_changed_key}")
        assert changed, "a real label change must not diff as empty"
        assert "{row_id}" in str(changed), f"the diff does not name the row that changed: {changed}"
```

**How to fill in the template:**

For each tool from Step 6, generate the appropriate test class:
- **READ tools:** `test_returns_entity_with_required_fields`, `test_not_found_returns_error`, `test_returns_correct_entity`
- **LIST tools:** `test_returns_list`, `test_filters_correctly`, `test_no_results_returns_empty_list`, `test_items_have_required_fields`
- **WRITE tools:** `test_success_response_shape`, `test_mutates_target_entity`, `test_updates_related_entity`, `test_creates_new_entity`, `test_rejects_nonexistent_target`, plus one test per business rule.
  A write that changes the field its row is found by (a move, a rename) is re-read by a key it does not change, or by the new value — never by the argument's old one.
- **Every success case:** its world satisfies every contract rule that governs that request (minimums, coverage, quotas, permissions), not only the rule under test; seed what each rule needs. A rule's parameters bind the success case too: a group the request leaves (after its filters) under a declared minimum size is that rule's failure, never a success. Each rule applies to every request variant; only a scope `verify_config` accepted narrows a rule (a `(source: …)` span naming the variant and the rule's code), and each rule gets one rule test per variant it governs (`rule_test_variant_missing`).
  Its arguments name rows `WORLD` seeds, in the state the request needs (a restore names a trashed row; an add names a member not yet present; a create names a path that does not exist).
  A delete never removes a row: the backend marks it `_deleted: true` and keeps it, so assert the flag on the target, or count only rows without it.
- **Tool chains:** One test per edge in the tool_graph
- **Constraints:** One test per enum, min/max, foreign key from the entity schemas
- **State machines:** One test per state machine the PRD names, each outcome from its producing line
- **Versioned entities** (only when the schema has revisions / branches / document versions):
  `test_seeded_record_ids_resolve`, `test_a_per_label_precondition_resolves_against_the_seed`,
  `test_revert_restores_prior_content`, `test_a_past_revision_is_immutable_after_later_writes`,
  `test_exactly_one_head`, `test_a_label_change_produces_a_nonempty_diff` — the six the
  shape tests cannot see (the rules they assert: "What a versioned store's seam mock must hold",
  below). The first two grade the SEED and everything after it grades a revision the test wrote, so
  dropping them leaves the one failure mode that is already present before any tool is called.

### What a versioned store's seam mock must hold

A versioned / HEAD-tracked store (revisions, branches, document versions) is faked by its seam mock,
never by harness code, so these rules are the MOCK's contract: they go into its `business_rules` /
`post_conditions`, and `TestVersionedEntities` pins them. Rules 1–3 hold for every versioned schema;
4 and 4b only when rows are separate entities (an inline-only store has no row entities to key).

1. **One HEAD, moved atomically.** A write that makes a new HEAD sets `is_head: true` on it and clears
   the previous HEAD in the same world write — never two HEADs, never none (`test_exactly_one_head`).
2. **The parent indexes its children in the same write.** A revision that owns rows carries the array
   of their STORE KEYS (`record_ids`), written with the rows; an id the world is not keyed on resolves
   to nothing (`test_seeded_record_ids_resolve`).
3. **One shape per field.** Rows live inline on the revision OR as separate row entities, never both;
   a reader may accept either shape, a writer writes one.
4. **Each revision owns a snapshot of its rows.** Row entities are keyed per (revision, row) —
   `<parent>_rev<N>_<row_id>`, the stable row id kept as a field inside — so a later write never
   changes a past revision (`test_a_past_revision_is_immutable_after_later_writes`,
   `test_revert_restores_prior_content`, `test_a_label_change_produces_a_nonempty_diff`).
4b. **Reads are point in time.** A row at revision N is its snapshot with the largest revision index
   ≤ N, never the latest one through a shared `row_id` index.
5. **Absence fails closed, and a rebuild keeps what it does not name.** A missing revision or row, or
   an unparseable revision target, is the store's not-found — never a shape-correct empty value or
   HEAD; a write that rebuilds a row carries forward every declared field the request does not set,
   and never inherits which revision the old row belonged to.

Every test docstring must explain:
- The precondition (GIVEN)
- What's being tested (WHEN)  
- What the backend must implement (THEN)
- WHY this matters for the agent (what breaks if the contract is violated)

## Also write `plurai_sim/conftest.py` — the "did this tier verify anything?" gate

Property 2 above needs a hook, and **a hook only fires from a `conftest.py`**: pytest
registers hooks from conftest files and installed plugins, never from a test module, so a
`pytest_sessionfinish` written into `test_mock_contract.py` is never called. It would be a gate
that reads correctly in review and gates nothing — the same shape as the tier it is meant to
fix. Emit this file alongside the tests, verbatim (it has no per-agent content):

```python
# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""Acceptance-tier gate: a run that verified NOTHING is a failed run.

Fixture setup that cannot reach the mock reports ERRORS, not failures, and a summary line
scanned for "failed" reads thirteen errored tests as a pass — which is how this tier reported
zero effective coverage while looking green. The exit code is the one signal a caller can read
without interpreting prose, so "nothing passed" has to move it.
"""

#: Distinct from pytest's own 1 (TESTS_FAILED) on purpose. Locally this tier is EXPECTED to
#: fail its business-rule tests against the dumb inline mocks, so exit 1 is normal there and a
#: gate that reused it would be unreadable — the operator could not tell the documented red
#: from a tier that verified nothing.
TIER_VERIFIED_NOTHING = 3

_TIER = "test_mock_contract.py"


def pytest_sessionfinish(session, exitstatus):
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is None:
        # `-p no:terminal` and some CI wrappers remove it. Raising here would fail runs that
        # verified everything.
        return

    def _tier(key):
        return [r for r in reporter.stats.get(key, []) if _TIER in getattr(r, "nodeid", "")]

    passed, errored, skipped = _tier("passed"), _tier("error"), _tier("skipped")
    # SCOPED to this tier's nodeids, and silent when it was not collected. A session-wide
    # count is satisfied by the customer's own passing tests, so on any repo-root `pytest`
    # the gate would wave 13 errored contract tests through — dead in exactly the run where
    # a caller is most likely to miss them.
    #
    # SKIPPED counts with errored and failed. A tier that skipped every test verified exactly
    # as much as one that errored on every test, and it is the quieter of the two: pytest's
    # own exit status for an all-skipped run is 0, so without this the gate is the only thing
    # that could have said so and it says nothing.
    if not passed and (errored or skipped or _tier("failed")):
        reporter.write_line(
            f"MOCK CONTRACT TIER DID NOT VERIFY ANYTHING: 0 passed, {len(errored)} errored, "
            f"{len(skipped)} skipped. A tier that verified nothing is not a clean one — treat "
            f"this as a FAILED acceptance run.",
            red=True,
        )
        session.exitstatus = TIER_VERIFIED_NOTHING
```

## And `plurai_sim/test_agent_envelope.py` — the agent-facing half, LOCAL ONLY

**Do not merge this into `test_mock_contract.py`.** That file is the ONE thing uploaded to the
backend, which copies it alone into `simulation_env/tests/` and runs it there. The harness
(`simulate.py` / `_mocks.py`) is not there, so an envelope test in that file fails on the missing
harness and reads as a contract failure — every repair iteration burning its budget on a missing
`simulate.py`, and the deploy withheld. It also does not belong there on the
merits: it grades the HARNESS's rendering, not the mock's behaviour.

```python
# This file belongs to you. Plurai generated it from your agent as a starting point;
# review it, change it and keep it with your code.
"""What the agent's React loop actually receives — the harness's half of the contract.

Asserting on the mock's raw HTTP response tests the mock in isolation. The agent never sees
that dict: `_mocks.py`'s dispatch wrapper turns it into a ToolMessage, and the wrapper is
where a whole class of harness defect lives — a mock that faithfully returned "requires at
least 4 samples per label" was rendered to the agent as "tool returned status=error", which
cost eleven probing calls in one turn, a wiped dataset, and two false-red policies. Every
raw-response assertion passed throughout.

LOCAL ONLY: never uploaded, because the backend has no harness to grade.
"""
import ast
import json
import logging
import pathlib
import types

import plurai
import pytest

from test_mock_contract import execute_tool, response_text, session  # noqa: F401 - `session` is the fixture


#: The harness's message-shaping surface — everything that stands between a tool result and
#: the ToolMessage the agent reads.
_SHAPING_FNS = {"_error_message", "_render_content", "_render_error_content", "_already_enveloped", "_tool_content"}


def _harness_trees():
    """`(filename, parsed source)` for each harness file present — `_mocks.py`, then the
    single-file layout's `simulate.py`.

    Both readers below take the SOURCE, never an import — see `_envelope_fns` for why — so the
    two-layout lookup is stated once here.
    """
    for name in ("_mocks.py", "simulate.py"):
        path = pathlib.Path(__file__).with_name(name)
        if path.exists():
            yield name, ast.parse(path.read_text(encoding="utf-8"))


def _envelope_fns():
    """The harness's own `_error_message` / `_render_*`, WITHOUT importing the agent.

    Lifted from SOURCE in both layouts — the function defs plus the module-level constants
    they read — rather than imported. `simulate.py`'s import IS the agent import; and
    `_mocks.py`, whose own docstring says importing it "registers descriptors with plurai
    (decorator side effect)", routinely imports the agent's DTO module too. Reading the
    source keeps this file's no-agent-import contract in BOTH layouts and needs none of the
    harness's runtime dependencies installed.

    Restating the functions here instead would be a copy that keeps passing on the day the
    wrapper drifts — which is exactly how the tier stayed green through the defect it was
    written to catch.
    """
    prefixes = ("_ENVELOPE_TEMPLATE", "_ERROR_TEMPLATE", "_PAYLOAD_FIELD", "_VERSION_FIELDS")
    for name, tree in _harness_trees():
        kept = [
            node for node in tree.body
            if (isinstance(node, ast.FunctionDef) and node.name in _SHAPING_FNS)
            or (
                isinstance(node, (ast.Assign, ast.AnnAssign))
                and any(
                    isinstance(t, ast.Name) and t.id.startswith(prefixes)
                    for t in (node.targets if isinstance(node, ast.Assign) else [node.target])
                )
            )
        ]
        if any(isinstance(n, ast.FunctionDef) and n.name == "_error_message" for n in kept):
            module = types.ModuleType("_plurai_envelope")
            module.__dict__["json"] = json
            module.__dict__["logging"] = logging
            module.__dict__["plurai"] = plurai  # the shaping helpers read the envelope through plurai.mock_result
            exec(compile(ast.Module(body=kept, type_ignores=[]), f"<{name} envelope>", "exec"), module.__dict__)
            return module
    pytest.fail(
        "neither _mocks.py nor simulate.py defines _error_message — the agent-facing "
        "envelope cannot be asserted, so this tier cannot see the defect class it exists for"
    )


def render(raw: dict, tool_name: str) -> tuple[str, str]:
    """`(status, content)` of the ToolMessage the agent would receive for `raw`.

    Takes the ALREADY-DISPATCHED response rather than re-calling the tool: a state-changing
    refusal is not the same call twice, so a helper that dispatched again would grade a
    different world than the one the test set up.

    Mirrors the dispatch wrapper's two branches exactly — detect with `_error_message`,
    render with `_render_error_content` or `_render_content`.
    """
    module = _envelope_fns()
    err_msg = module._error_message(raw)
    if err_msg is not None:
        return "error", module._render_error_content(tool_name, err_msg)
    return "success", module._render_content(raw)


def _is_falsy_literal(node):
    """A literal the caller cannot tell apart from "nothing came back"."""
    if isinstance(node, ast.Constant):
        return node.value is None or node.value == "" or node.value == 0 or node.value is False
    return isinstance(node, (ast.Dict, ast.List, ast.Set, ast.Tuple)) and not (
        getattr(node, "elts", None) or getattr(node, "keys", None)
    )


def _is_broad(node):
    """`except:`, `except Exception:`, `except (Exception, ...):` — the catch-alls."""
    if node is None:
        return True
    if isinstance(node, ast.Name):
        return node.id in ("Exception", "BaseException")
    return isinstance(node, ast.Tuple) and any(_is_broad(e) for e in node.elts)


def _call_name(call):
    """What a call targets, bare: `print(...)` and `log.warning(...)` both name themselves."""
    func = call.func
    return func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")


def _says_something(node):
    """Whether this block reports what it swallowed. A LOUD fallback leaves a trace in the run,
    which is the same carve-out `verify_simulate` already makes for the dotenv import."""
    reporters = {"print", "warn", "warning", "error", "exception", "log", "debug", "info", "critical"}
    return any(isinstance(inner, ast.Call) and _call_name(inner) in reporters for inner in ast.walk(node))


def _is_silent(block):
    """A block that neither re-raises, nor reports, nor returns anything a caller can act on.

    Defined by what SURVIVES it rather than by matching statement shapes: `return None` was the
    only shape the first cut caught, while `return ''` — the one this tier's own docstring
    describes — and `return {}`, an assign-then-return, and a bare `pass` all sailed through.
    """
    for inner in ast.walk(block):
        if isinstance(inner, ast.Raise):
            return False
        if isinstance(inner, ast.Return) and inner.value is not None and not _is_falsy_literal(inner.value):
            return False
        if isinstance(inner, ast.Assign) and not _is_falsy_literal(inner.value):
            return False
    return not _says_something(block)


def _swallows(node):
    """Whether this statement swallows an exception silently, in either shape it takes."""
    if isinstance(node, ast.ExceptHandler):
        return _is_broad(node.type) and _is_silent(node)
    # `with contextlib.suppress(Exception):` is the same swallow with no handler to read — the
    # body simply stops and the caller gets an implicit None.
    return isinstance(node, ast.With) and any(
        isinstance(item.context_expr, ast.Call)
        and _call_name(item.context_expr) == "suppress"
        and any(_is_broad(arg) for arg in item.context_expr.args)
        for item in node.items
    )


def _swallowing_handlers():
    """`(function, line)` for every place a shaping helper swallows its input silently.

    Read from SOURCE for the same reason the envelope is: the defect is invisible at runtime on
    well-formed input, and well-formed input is the only kind a passing tier ever feeds it.
    """
    found = []
    for name, tree in _harness_trees():
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) or fn.name not in _SHAPING_FNS:
                continue
            for node in ast.walk(fn):
                if _swallows(node):
                    found.append((f"{name}:{fn.name}", node.lineno))
    return found


def test_no_message_shaping_helper_swallows_its_input():
    """A shaping helper that swallows a malformed tool result returns a well-formed NOTHING:
    the agent is handed an empty render, every downstream verdict grades that silence, and no
    artifact anywhere records that the input was broken. A harness fault has to read as a
    harness fault — let it RAISE, or return a diagnostic the render carries. Narrow the except
    to what you actually expect, or say out loud what you fell back to; SILENT is the defect."""
    _envelope_fns()  # FAIL LOUD first: with no shaping surface to read, silence proves nothing
    swallows = _swallowing_handlers()
    assert not swallows, (
        f"message-shaping helper(s) silently swallow their input: {swallows}. "
        "Every verdict downstream then grades an empty render instead of a broken input."
    )


class TestAgentEnvelope:
    """Pick, for `{error_tool}`/`{error_args}`, a call whose refusal the mock makes with a
    SPECIFIC diagnostic (a constraint violation, a not-found, a business-rule refusal) worded
    by its producing line — not a generic one. `{error_substring}` is a distinctive phrase out of it.
    An assertion that would also pass on a constant ("mocked tool failed") pins nothing."""

    def test_an_error_reaches_the_agent_as_status_error(self, session):
        """A mocked failure rendered as a SUCCESS ToolMessage whose body says it failed does
        not stop the React loop — the agent rephrases and retries to GraphRecursionError."""
        raw = execute_tool(session, "{error_tool}", {error_args})
        status, _content = render(raw, "{error_tool}")
        assert status == "error"

    def test_the_mocks_own_diagnostic_survives_into_the_ToolMessage(self, session):
        """The raw response carries the reason; the agent has to be TOLD it. An agent told
        only THAT a call failed reverse-engineers the cause by probing — eleven calls in one
        turn, a wiped dataset, and two policies red on the harness's own damage."""
        raw = execute_tool(session, "{error_tool}", {error_args})
        assert "{error_substring}" in response_text(raw), \
            "fixture drift: the mock no longer returns this diagnostic — re-pick {error_substring}"
        _status, content = render(raw, "{error_tool}")
        assert "{error_substring}" in content

    def test_a_success_reaches_the_agent_with_its_fields_intact(self, session):
        """The other half of the same contract: a renderer that selects one field hands the
        model an empty-looking success on a call that worked."""
        raw = execute_tool(session, "{read_tool}", {read_args})
        status, content = render(raw, "{read_tool}")
        assert status == "success"
        for field in {agent_read_fields}:
            # The QUOTED key: a bare substring passes on "user_id" when the agent reads "id".
            assert f'"{field}"' in content, f"the agent reads {field!r} and the envelope dropped it"
        payload = raw.get("content", raw) if isinstance(raw, dict) else raw  # the tool's return; siblings never render
        assert len(content) >= len(json.dumps(payload)) // 2, \
            "the rendered content is far smaller than the tool's return — a field-selecting renderer"
```
