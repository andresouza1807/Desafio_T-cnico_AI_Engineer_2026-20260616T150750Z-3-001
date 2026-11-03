"""Slice 07-D offline integration with the real OpenAI SDK (docs/plans/07-agent.md, D).

Cases AG94, AG95 and AG118. `OpenAIResponsesProvider` runs the installed `openai` SDK over
`httpx2.MockTransport`: the transport records the JSON the SDK actually sends and answers
with literal HTTP bodies written in this module. Below the adapter everything is real:
the agent loop, the session, `execute_tool`, SQL over SQLite files built in `tmp_path` by
the real `prepare_database`. Every `OPENAI_*` variable is removed, sockets and DNS raise
(autouse guard here, and a guard installed before any import in the AG95 subprocess), and
the only key is the literal "test-key-not-a-secret". Offline tests do not prove model
access, provider acceptance of schemas or resent items (spec Q-07), language quality or a
token bound derived from bytes.
"""

import hashlib
import json
import os
import socket
import subprocess
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest

import emporio_agent
import emporio_agent.agent as agent_module
from emporio_agent.agent import Agent, AgentResources
from emporio_agent.openai_provider import OpenAIResponsesProvider
from emporio_agent.provider import (
    FunctionCall,
    FunctionCallOutput,
    ProviderItem,
    UserMessage,
)
from emporio_agent.session import ConversationSession

KEY = "test-key-not-a-secret"
MODEL = "gpt-5.4-mini"
SNAPSHOT = "gpt-5.4-mini-2026-03-17"
URL = "https://api.openai.com/v1/responses"
CLOCK = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
PREFIX = "desafio_tecnico_ai_eng - "
PREPARED_AT = "2026-11-02T12:00:00+00:00"
REPO = Path(__file__).resolve().parents[2]
TOO_MUCH_TEXT = (
    "A pergunta exigiu consultas ou contexto demais. Faça uma pergunta mais específica ou "
    "inicie uma nova conversa."
)
INPUT_INVALID_TEXT = (
    "Não consegui ler a sua mensagem porque ela contém caracteres inválidos. "
    "Escreva a mensagem novamente."
)
ANONYMOUS_LINE = (
    "Session: no customer is linked to this session. Order lookups are unavailable; "
    "do not ask the customer to identify themselves by message."
)

# Literal provider items, compact, in provider key order (raw must equal them, spec T1).
REASONING_1 = (
    '{"type":"reasoning","id":"rs_94","summary":[{"type":"summary_text","text":"Cotar no PIX"}],'
    '"encrypted_content":"gAAAAB-ENC-94+/==","future_field":{"kept":true,"n":[1,2]}}'
)
QUOTE_ARGS = '{"product_id":201,"payment_method":"pix"}'
CALL_1 = (
    r'{"type":"function_call","id":"fc_94","call_id":"call_94","name":"quote_product",'
    r'"arguments":"{\"product_id\":201,\"payment_method\":\"pix\"}","status":"completed"}'
)
ANSWER_94 = "O Violão Clássico Alfa sai por R$ 854,91 no PIX."
MESSAGE_2 = (
    '{"type":"message","id":"msg_94","role":"assistant","status":"completed",'
    '"phase":"final_answer","content":[{"type":"output_text","text":"' + ANSWER_94 + '",'
    '"annotations":[]}]}'
)
USAGE = '{"input_tokens":120,"output_tokens":30,"total_tokens":150}'


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    for name in [name for name in os.environ if name.startswith("OPENAI_")]:
        monkeypatch.delenv(name)

    def guard(*args, **kwargs):
        raise AssertionError("network access attempted")

    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, guard)
    monkeypatch.setattr(socket.socket, "connect", guard)
    monkeypatch.setattr(socket.socket, "connect_ex", guard)


# --- helpers (test-only) --------------------------------------------------------------


class Transport:
    """MockTransport handler: records every request, answers with the next literal body."""

    def __init__(self, *bodies: bytes) -> None:
        self.bodies = list(bodies)
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.bodies:
            raise AssertionError("unscripted HTTP request")
        number = len(self.requests)
        return httpx2.Response(
            200, content=self.bodies.pop(0), headers={"x-request-id": f"req_{number}"}
        )

    def sent(self, index: int) -> dict:
        """The JSON body the SDK actually put on the wire for request `index`."""
        return json.loads(self.requests[index].content)


def body(*output: str, rid: str = "resp_1", usage: str = USAGE) -> bytes:
    text = (
        '{"id":"' + rid + '","object":"response","status":"completed",'
        '"model":"' + SNAPSHOT + '","output":[' + ",".join(output) + '],"usage":' + usage + "}"
    )
    return text.encode("utf-8")


def make_provider(transport: Transport) -> tuple[OpenAIResponsesProvider, httpx2.Client]:
    client = httpx2.Client(transport=httpx2.MockTransport(transport))
    return OpenAIResponsesProvider(api_key=KEY, model=MODEL, http_client=client), client


def make_agent(provider, database: Path, index: Path) -> Agent:
    ticks = iter(range(10_000))

    def forbidden_sleep(_seconds: float) -> None:
        raise AssertionError("no retry is scripted in these cases")

    return Agent(
        provider,
        AgentResources(database_path=database, policy_index_path=index, clock=lambda: CLOCK),
        sleep=forbidden_sleep,
        monotonic=lambda: float(next(ticks)),
    )


def tool_spy(monkeypatch) -> list[tuple[str, str, str]]:
    """Delegating spy on the agent's execute_tool: (name, arguments, payload)."""
    real = agent_module.execute_tool
    calls: list[tuple[str, str, str]] = []

    def observed(name, arguments, context):
        execution = real(name, arguments, context)
        calls.append((name, arguments, execution.payload))
        return execution

    monkeypatch.setattr(agent_module, "execute_tool", observed)
    return calls


def state(session: ConversationSession) -> tuple[object, ...]:
    return (session.history, session.history_bytes, session.pruned_turns, session.customer)


def compact(value: object) -> str:
    """Spec 06 G9 serialization, applied by the standard library to a literal dict."""
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def provenance(base: Path, tables: tuple[tuple[str, int], ...]) -> dict[str, object]:
    sources = []
    for table, row_count in tables:
        written = (base / "src" / f"{PREFIX}{table}.csv").read_bytes()
        sources.append(
            {
                "table_name": table,
                "file_name": f"{PREFIX}{table}.csv",
                "sha256": hashlib.sha256(written).hexdigest(),
                "size_bytes": len(written),
                "row_count": row_count,
            }
        )
    return {"prepared_at": PREPARED_AT, "sources": sources}


def catalog_provenance(base: Path) -> dict[str, object]:
    return provenance(base, (("categories", 3), ("products", 17), ("promotions", 9)))


def violao(product_id, name, list_price, effective_price, promotion, stock):
    return {
        "product_id": product_id,
        "name": name,
        "category": {"category_id": 1, "name": "Violões"},
        "list_price": list_price,
        "effective_price": effective_price,
        "promotion": promotion,
        "stock_quantity": stock,
        "status": "active",
        "availability": "available",
        "is_available": True,
        "quality_notes": [],
    }


def quote_201_pix_payload(base: Path) -> str:
    """No promotion: PIX gives 5% (899.90 -> 854.91), Spec 03 / report 06 literals."""
    data = {
        "product": violao(201, "Violão Clássico Alfa", "899.90", "899.90", None, 4),
        "payment_method": "pix",
        "list_price": "899.90",
        "final_price": "854.91",
        "discount_percent": "5.00",
        "discount_source": "pix",
        "promotion": None,
        "pix_not_combined": False,
        "provenance": catalog_provenance(base),
    }
    return compact({"tool": "quote_product", "status": "ok", "data": data, "error": None})


def search_violoes_payload(base: Path) -> str:
    """Spec 06 example: violões, budget 1000.00 -> 9 matches, first page 211..201."""
    zero = {"promotion_id": 302, "discount_percent": "0.00", "description": "Campanha Zero"}
    data = {
        "products": [
            violao(
                211,
                "Violão Lambda",
                "250.00",
                "0.00",
                {"promotion_id": 303, "discount_percent": "100.00", "description": "Brinde Total"},
                1,
            ),
            violao(214, "Violão Ômega", "19.90", "19.90", None, 1),
            violao(
                212,
                "Violão Mu",
                "333.33",
                "266.66",
                {"promotion_id": 305, "discount_percent": "20.00", "description": "Promo Vinte"},
                1,
            ),
            violao(210, "Violão Kappa 0%", "700.00", "700.00", zero, 2),
            violao(201, "Violão Clássico Alfa", "899.90", "899.90", None, 4),
        ],
        "total_matches": 9,
        "offset": 0,
        "page_size": 5,
        "next_offset": 5,
        "available_only": True,
        "max_price_brl": "1000.00",
        "category_resolution": "resolved",
        "category": {"category_id": 1, "name": "Violões"},
        "candidate_categories": [],
        "provenance": catalog_provenance(base),
    }
    return compact({"tool": "search_products", "status": "ok", "data": data, "error": None})


def order_5001_payload(base: Path) -> str:
    """Spec 06 example: order 5001, customer 1, clock 2026-11-03T15:00:00+00:00."""

    def item(product_id, name, quantity):
        return {
            "product_id": product_id,
            "name": name,
            "quantity": quantity,
            "name_source": "current_catalog",
        }

    data = {
        "order_id": 5001,
        "status": "shipped",
        "items": [item(10, "Violão Teste Alfa", 2), item(11, "Teclado Teste Beta", 1)],
        "total": "1500.00",
        "tracking_code": "TRK-5001",
        "estimated_delivery": "2026-11-03",
        "estimate_status": "due_today",
        "business_date": "2026-11-03",
        "provenance": provenance(base, (("orders", 6), ("order_items", 6), ("products", 5))),
    }
    return compact({"tool": "get_order", "status": "ok", "data": data, "error": None})


@pytest.fixture
def catalog_base(tmp_path, catalog_kit) -> Path:
    base = tmp_path / "catalogo"
    catalog_kit.build(base)
    return base


@pytest.fixture
def catalog_db(catalog_base, catalog_kit) -> Path:
    return catalog_base / catalog_kit.DB_DIR / catalog_kit.DB_NAME


@pytest.fixture
def absent_index(tmp_path) -> Path:
    return tmp_path / "sem-indice" / "politicas.json"


# --- AG94: real SDK, reasoning and call preserved, real tool output sent --------------


def test_ag94_sdk_round_trip_preserves_reasoning_call_and_real_output(
    catalog_base, catalog_db, absent_index, monkeypatch
):
    calls = tool_spy(monkeypatch)
    transport = Transport(
        body(REASONING_1, CALL_1, rid="resp_94a"),
        body(MESSAGE_2, rid="resp_94b"),
        body(
            '{"type":"message","id":"msg_94c","role":"assistant","status":"completed",'
            '"content":[{"type":"output_text","text":"De nada!","annotations":[]}]}',
            rid="resp_94c",
        ),
    )
    provider, client = make_provider(transport)
    session = ConversationSession()
    user = "Quanto fica o violão 201 no PIX?"

    with provider:
        agent = make_agent(provider, catalog_db, absent_index)
        result = agent.respond(session, user)
        payload = quote_201_pix_payload(catalog_base)

        assert (result.outcome, result.failure, result.text) == ("answered", None, ANSWER_94)
        assert calls == [("quote_product", QUOTE_ARGS, payload)]
        assert len(transport.requests) == 2
        assert [str(r.url) for r in transport.requests] == [URL, URL]
        first, second = transport.sent(0), transport.sent(1)
        assert first["input"] == [{"type": "message", "role": "user", "content": user}]
        # Request 2: the reasoning item (encrypted_content and the unknown field), the call,
        # then the function_call_output with the same call_id and the real tool payload.
        assert second["input"] == [
            {"type": "message", "role": "user", "content": user},
            {
                "type": "reasoning",
                "id": "rs_94",
                "summary": [{"type": "summary_text", "text": "Cotar no PIX"}],
                "encrypted_content": "gAAAAB-ENC-94+/==",
                "future_field": {"kept": True, "n": [1, 2]},
            },
            {
                "type": "function_call",
                "id": "fc_94",
                "call_id": "call_94",
                "name": "quote_product",
                "arguments": QUOTE_ARGS,
                "status": "completed",
            },
            {"type": "function_call_output", "call_id": "call_94", "output": payload},
        ]
        assert list(second["input"][1]) == [
            "type",
            "id",
            "summary",
            "encrypted_content",
            "future_field",
        ]
        assert second["input"][3]["output"].encode("utf-8") == payload.encode("utf-8")
        for sent in (first, second):
            assert sent["model"] == MODEL
            assert sent["tool_choice"] == "auto"
            assert sent["parallel_tool_calls"] is False
            assert sent["store"] is False
            assert sent["max_output_tokens"] == 8000
            assert sent["instructions"].endswith("\n\n" + ANONYMOUS_LINE)
            assert "previous_response_id" not in sent
        assert second["tools"] == first["tools"]

        # Commit: the exact provider items, in order, then the final message.
        turn = (
            UserMessage(user),
            ProviderItem("reasoning", REASONING_1),
            FunctionCall("call_94", "quote_product", QUOTE_ARGS, CALL_1),
            FunctionCallOutput("call_94", payload),
            ProviderItem("assistant_message", MESSAGE_2),
        )
        assert [t.items for t in session.history] == [turn]
        diagnostics = result.diagnostics
        assert [
            (a.index, a.outcome, a.reported_model, a.request_id) for a in diagnostics.attempts
        ] == [
            (1, "response", SNAPSHOT, "req_1"),
            (2, "response", SNAPSHOT, "req_2"),
        ]
        assert diagnostics.configured_model == MODEL
        assert [r.references for r in diagnostics.tool_runs] == [("product:201",)]
        assert (diagnostics.input_tokens_reported, diagnostics.output_tokens_reported) == (240, 60)

        # The retained turn is resent by the SDK on the next message, unchanged.
        follow = agent.respond(session, "obrigado")
        assert (follow.outcome, follow.text) == ("answered", "De nada!")
        third = transport.sent(2)
        assert third["input"][:5] == second["input"] + [json.loads(MESSAGE_2)]
        assert third["input"][5] == {"type": "message", "role": "user", "content": "obrigado"}
        assert len(third["input"]) == 6
        assert b"gAAAAB-ENC-94+/==" in transport.requests[2].content
    assert client.is_closed


# --- AG118: limits stop HTTP before it is sent -----------------------------------------

GREETING = (
    '{"type":"message","id":"msg_ola","role":"assistant","status":"completed",'
    '"content":[{"type":"output_text","text":"Olá!","annotations":[]}]}'
)
BUDGET_PAD = 261_798
BUDGET_REASONING = (
    '{"type":"reasoning","id":"rs_g","summary":[],"encrypted_content":"' + "x" * BUDGET_PAD + '"}'
)
SEARCH_ARGS = '{"category":"violões","max_price_brl":"1000.00"}'
BUDGET_CALL = (
    r'{"type":"function_call","id":"fc_g","call_id":"call_g","name":"search_products",'
    r'"arguments":"{\"category\":\"violões\",\"max_price_brl\":\"1000.00\"}","status":"completed"}'
)


def test_ag118_literal_sizes_match_their_strings(catalog_base):
    assert len(GREETING.encode("utf-8")) == 140  # "Olá!": á is 2 bytes
    assert len(BUDGET_REASONING.encode("utf-8")) == 68 + 261_798
    assert len(BUDGET_CALL.encode("utf-8")) == 173
    assert len(search_violoes_payload(catalog_base).encode("utf-8")) == 2_435


@pytest.fixture
def greeted(catalog_db, absent_index, monkeypatch):
    """A session with one answered turn through the real SDK, and its transport."""
    calls = tool_spy(monkeypatch)
    transport = Transport(body(GREETING), body(BUDGET_REASONING, BUDGET_CALL, rid="resp_g"))
    provider, client = make_provider(transport)
    agent = make_agent(provider, catalog_db, absent_index)
    session = ConversationSession()
    result = agent.respond(session, "oi")
    assert (result.outcome, result.text) == ("answered", "Olá!")
    assert session.history_bytes == 2 + 140  # "oi" + GREETING
    yield agent, session, transport, calls
    provider.close()
    assert client.is_closed


def test_ag118_lone_surrogate_sends_no_http_request(greeted):
    agent, session, transport, calls = greeted
    before = state(session)
    sent_before = len(transport.requests)

    result = agent.respond(session, "oi \ud800")

    assert (result.outcome, result.failure, result.text) == (
        "rejected_input",
        "input_invalid_text",
        INPUT_INVALID_TEXT,
    )
    assert sent_before == 1
    assert len(transport.requests) == sent_before
    assert result.diagnostics.attempts == ()
    assert calls == []
    assert state(session) == before


def test_ag118_chain_over_budget_before_next_attempt_sends_no_more_http(greeted, catalog_base):
    agent, session, transport, calls = greeted
    before = state(session)
    sent_before = len(transport.requests)
    # Chain before tools: "busca" 5 + reasoning 261,866 + call 173 = 262,044 (fits).
    # After the real tool output ("call_g" 6 + 2,435 bytes): 264,485 > 262,144.
    assert 5 + 68 + 261_798 + 173 == 262_044
    assert 262_044 + 6 + 2_435 == 264_485

    result = agent.respond(session, "busca")

    assert (result.outcome, result.failure, result.text) == (
        "failed",
        "request_too_large",
        TOO_MUCH_TEXT,
    )
    assert result.diagnostics.budget_failure == "chain"
    # Exactly the one request of attempt 1; no request for the attempt that did not fit.
    assert len(transport.requests) == sent_before + 1
    assert transport.bodies == []
    assert [a.request_bytes for a in result.diagnostics.attempts] == [6_711 + 142 + 5]
    # The tool of the response that fitted did run once, with the real envelope.
    assert calls == [("search_products", SEARCH_ARGS, search_violoes_payload(catalog_base))]
    sent = transport.sent(sent_before)
    assert sent["input"][-1] == {"type": "message", "role": "user", "content": "busca"}
    assert state(session) == before


# --- AG95: isolated subprocess, guards before imports, full turn ----------------------

_CHILD = textwrap.dedent(
    r"""
    import json
    import os
    import socket
    import sys

    attempts = []

    def guard(*args, **kwargs):
        attempts.append(1)
        raise AssertionError("network access attempted")

    class GuardSocket(socket.socket):
        # A class, not a function: modules such as ssl subclass socket.socket on import.
        def __init__(self, *args, **kwargs):
            guard()

    socket.socket = GuardSocket
    for name in (
        "create_connection",
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
        "gethostbyaddr",
        "socketpair",
    ):
        setattr(socket, name, guard)

    src, database, index = sys.argv[1:4]
    sys.path.insert(0, src)  # explicit import root: -I ignores PYTHONPATH

    report = {"core_imports": {}}
    import emporio_agent.provider
    report["core_imports"]["provider"] = "openai" in sys.modules
    import emporio_agent.session
    report["core_imports"]["session"] = "openai" in sys.modules
    import emporio_agent.instructions
    report["core_imports"]["instructions"] = "openai" in sys.modules
    import emporio_agent.agent
    report["core_imports"]["agent"] = "openai" in sys.modules

    import emporio_agent.openai_provider
    report["openai_after_adapter"] = "openai" in sys.modules

    from datetime import UTC, datetime
    from pathlib import Path

    import httpx2

    from emporio_agent.agent import Agent, AgentResources
    from emporio_agent.openai_provider import OpenAIResponsesProvider
    from emporio_agent.services.orders import CustomerContext
    from emporio_agent.session import ConversationSession

    CALL = (
        '{"type":"function_call","id":"fc_95","call_id":"call_95","name":"get_order",'
        '"arguments":"{\\"order_id\\":5001}","status":"completed"}'
    )
    MESSAGE = (
        '{"type":"message","id":"msg_95","role":"assistant","status":"completed",'
        '"content":[{"type":"output_text","text":"Seu pedido 5001 foi enviado.",'
        '"annotations":[]}]}'
    )

    def body(item):
        return (
            '{"id":"resp_95","object":"response","status":"completed",'
            '"model":"gpt-5.4-mini","output":[' + item + "]}"
        ).encode("utf-8")

    bodies = [body(CALL), body(MESSAGE)]
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx2.Response(200, content=bodies.pop(0))

    client = httpx2.Client(transport=httpx2.MockTransport(handler))
    provider = OpenAIResponsesProvider(
        api_key="test-key-not-a-secret", model="gpt-5.4-mini", http_client=client
    )
    with provider:
        agent = Agent(
            provider,
            AgentResources(
                database_path=Path(database),
                policy_index_path=Path(index),
                clock=lambda: datetime(2026, 11, 3, 15, 0, tzinfo=UTC),
            ),
        )
        session = ConversationSession(customer=CustomerContext(1))
        result = agent.respond(session, "status do pedido 5001")

    report.update(
        {
            "outcome": result.outcome,
            "failure": result.failure,
            "text": result.text,
            "requests": len(sent),
            "function_call_output": sent[1]["input"][-1] if len(sent) > 1 else None,
            "history_turns": len(session.history),
            "references": [list(run.references) for run in result.diagnostics.tool_runs],
            "client_closed": client.is_closed,
            "attempts": len(attempts),
            "isolated": sys.flags.isolated,
            "prefix": sys.prefix,
            "package_file": emporio_agent.provider.__file__,
            "env": sorted(key for key in os.environ if key.startswith("OPENAI")),
        }
    )
    print(json.dumps(report))
    """
)


def venv_python() -> Path:
    relative = Path("Scripts", "python.exe") if os.name == "nt" else Path("bin", "python")
    return REPO / ".venv" / relative


def test_ag95_isolated_subprocess_full_turn_without_network(tmp_path, orders_kit):
    base = tmp_path / "pedidos"
    database = orders_kit.build(base)  # preparation happens here, before the subprocess
    index = tmp_path / "sem-indice" / "politicas.json"
    script = tmp_path / "turno_isolado.py"
    script.write_text(_CHILD, encoding="utf-8")
    src = Path(emporio_agent.__file__).resolve().parents[1]
    assert src == REPO / "src"
    python = venv_python()
    assert python.is_file()
    env = {key: value for key, value in os.environ.items() if not key.startswith("OPENAI_")}

    completed = subprocess.run(
        [str(python), "-I", str(script), str(src), str(database), str(index)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert report["core_imports"] == {
        "provider": False,
        "session": False,
        "instructions": False,
        "agent": False,
    }
    assert report["openai_after_adapter"] is True
    assert (report["outcome"], report["failure"], report["text"]) == (
        "answered",
        None,
        "Seu pedido 5001 foi enviado.",
    )
    assert report["requests"] == 2
    assert report["function_call_output"] == {
        "type": "function_call_output",
        "call_id": "call_95",
        "output": order_5001_payload(base),
    }
    assert report["history_turns"] == 1
    assert report["references"] == [["order"]]
    assert report["client_closed"] is True
    assert report["attempts"] == 0
    assert report["isolated"] == 1
    assert Path(report["prefix"]).resolve() == (REPO / ".venv").resolve()
    assert Path(report["package_file"]).resolve().is_relative_to(src)
    assert report["env"] == []
