"""End-to-end test of the Telegram human loop.

The rest of the suite covers the *rejection* paths — an unsigned callback, a
foreign chat. This file covers the path that actually has to work on the day:
a held action becomes a card, the card is delivered, a human taps a button, and
the action resolves.

It runs against a stand-in for Telegram's Bot API rather than the real service,
which is possible because the API base is configurable. The stand-in records
every outbound call, so the assertions are about what Membrane genuinely sent —
not about what we hoped it sent.
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from membrane import approvals, telegram
from membrane.config import get_settings
from membrane.contracts import sign_callback
from membrane.db import session_scope
from membrane.models import ApprovalStatus, Sensitivity

CHAT_ID = "987654321"


# --------------------------------------------------------------------------
# a stand-in for api.telegram.org
# --------------------------------------------------------------------------


class FakeTelegram:
    """Records calls and answers them the way the real Bot API does."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.message_id = 4242
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base(self) -> str:
        assert self._server is not None
        host, port = self._server.server_address
        return f"http://127.0.0.1:{port}"

    def calls_to(self, method: str) -> list[dict]:
        return [payload for name, payload in self.calls if name == method]

    def start(self) -> "FakeTelegram":
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("content-length", 0))
                body = json.loads(self.rfile.read(length) or b"{}")
                method = self.path.rsplit("/", 1)[-1]
                recorder.calls.append((method, body))

                result: object = True
                if method == "sendMessage":
                    result = {"message_id": recorder.message_id}

                payload = json.dumps({"ok": True, "result": result}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):  # keep the test output clean
                return

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


@pytest.fixture(autouse=True)
async def _schema():
    """Several tests here write through session_scope() without going via the
    app, so they do not get the schema from the lifespan hook."""
    from membrane.db import init_db

    await init_db()


@pytest.fixture
def fake_telegram(monkeypatch):
    """Point Membrane at the stand-in and give it a bot token to use."""
    server = FakeTelegram().start()
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_api_base", server.base)
    monkeypatch.setattr(settings, "telegram_bot_token", "123456:TEST-TOKEN")
    monkeypatch.setattr(settings, "telegram_chat_id", CHAT_ID)
    try:
        yield server
    finally:
        server.stop()


async def _make_held_action(session_id: str, *, expires_in: float | None = None,
                            **card_extra):
    """Create a pending card.

    `expires_in` sets the deadline explicitly rather than relying on the
    configured timeout — the janitor expires cards on a timer, and a test that
    depends on cached settings is a test that fails when the suite order
    changes.
    """
    from datetime import timedelta

    from membrane.models import utcnow

    async with session_scope() as db:
        action = await approvals.create(
            db,
            session_id=session_id,
            tool="payment.transfer",
            capability="payment.transfer",
            sensitivity=Sensitivity.IRREVERSIBLE,
            reason="argument 'iban' traces to untrusted content from an invoice",
            card=approvals.build_card(
                tool="payment.transfer",
                capability="payment.transfer",
                sensitivity=Sensitivity.IRREVERSIBLE,
                args={"iban": "DE89370400440532013000", "amount": "4800.00"},
                reason="argument 'iban' traces to untrusted content",
                **card_extra,
            ),
        )
        if expires_in is not None:
            action.expires_at = utcnow() + timedelta(seconds=expires_in)
        return action.id


# --------------------------------------------------------------------------
# outbound: the card
# --------------------------------------------------------------------------


def test_the_bot_is_detected_as_configured(fake_telegram):
    assert telegram.configured() is True


@pytest.mark.asyncio
async def test_a_held_action_is_delivered_as_a_card(fake_telegram, session_id):
    action_id = await _make_held_action(session_id)

    async with session_scope() as db:
        action = await approvals.get(db, action_id)
        message_id = await telegram.push_card(action)

    assert message_id == "4242", "the Telegram message id should come back"

    sent = fake_telegram.calls_to("sendMessage")
    assert len(sent) == 1
    payload = sent[0]

    assert payload["chat_id"] == CHAT_ID
    assert payload["parse_mode"] == "HTML"

    # Two buttons, both carrying a signed callback bound to this action.
    buttons = payload["reply_markup"]["inline_keyboard"][0]
    assert len(buttons) == 2
    approve, deny = buttons
    assert approve["callback_data"] == sign_callback(action_id, "approve")
    assert deny["callback_data"] == sign_callback(action_id, "deny")
    # Telegram rejects callback_data over 64 bytes.
    for button in buttons:
        assert len(button["callback_data"].encode()) <= 64


@pytest.mark.asyncio
async def test_the_card_carries_the_four_things_and_no_content(
    fake_telegram, session_id
):
    action_id = await _make_held_action(session_id)
    async with session_scope() as db:
        action = await approvals.get(db, action_id)
        await telegram.push_card(action)

    text = fake_telegram.calls_to("sendMessage")[0]["text"]

    assert "What" in text and "Why" in text
    assert "payment.transfer" in text
    assert "irreversible" in text
    assert "Diff against the signed intent" in text
    assert "treated as DENY" in text

    # The value itself never leaves — only its shape and a digest.
    assert "DE89370400440532013000" not in text
    assert "IBAN in DE ending 3000" in text


@pytest.mark.asyncio
async def test_delivery_failure_leaves_the_action_held(session_id, monkeypatch):
    """A Telegram outage must not become an approval."""
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_api_base", "http://127.0.0.1:1")
    monkeypatch.setattr(settings, "telegram_bot_token", "123456:TEST-TOKEN")
    monkeypatch.setattr(settings, "telegram_chat_id", CHAT_ID)

    action_id = await _make_held_action(session_id)
    async with session_scope() as db:
        action = await approvals.get(db, action_id)
        message_id = await telegram.push_card(action)
        assert message_id is None

        still = await approvals.get(db, action_id)
        assert still.status is ApprovalStatus.PENDING


# --------------------------------------------------------------------------
# inbound: the callback
# --------------------------------------------------------------------------


def _callback(action_id: str, decision: str, *, chat=CHAT_ID, data=None):
    return {
        "callback_query": {
            "id": "cbq_1",
            "data": data or sign_callback(action_id, decision),
            "from": {"id": 11, "username": "operator"},
            "message": {"message_id": 4242, "chat": {"id": chat}},
        }
    }


@pytest.mark.asyncio
async def test_a_signed_deny_resolves_the_action(fake_telegram, client, session_id):
    action_id = await _make_held_action(session_id)

    # Deliver the card first, exactly as the pipeline does — that is what
    # records the message id the edit later needs.
    async with session_scope() as db:
        action = await approvals.get(db, action_id)
        action.telegram_message_id = await telegram.push_card(action)

    response = client.post("/v1/telegram/webhook",
                           json=_callback(action_id, "deny"))
    assert response.status_code == 200

    async with session_scope() as db:
        action = await approvals.get(db, action_id)
    assert action.status is ApprovalStatus.DENIED
    assert action.resolved_by == "telegram:operator"

    # The operator gets an acknowledgement and the card is stripped of buttons.
    assert fake_telegram.calls_to("answerCallbackQuery")
    edits = fake_telegram.calls_to("editMessageText")
    assert edits and "DENIED" in edits[0]["text"]


@pytest.mark.asyncio
async def test_a_resolved_card_cannot_be_reused_even_if_the_edit_failed(
    fake_telegram, client, session_id
):
    """The security property does not depend on the message edit succeeding.

    If Telegram is unreachable when we try to strip the buttons, the old card
    stays on screen with its keyboard intact. Tapping it must still do nothing,
    because authority lives in the action's state, not in the chat.
    """
    action_id = await _make_held_action(session_id)   # never delivered, no message id
    client.post("/v1/telegram/webhook", json=_callback(action_id, "deny"))

    async with session_scope() as db:
        assert (await approvals.get(db, action_id)).status is ApprovalStatus.DENIED

    # The buttons were never stripped — there was no message to strip.
    assert not fake_telegram.calls_to("editMessageText")

    # Tap the stale button anyway.
    client.post("/v1/telegram/webhook", json=_callback(action_id, "approve"))
    async with session_scope() as db:
        assert (await approvals.get(db, action_id)).status is ApprovalStatus.DENIED


@pytest.mark.asyncio
async def test_a_signed_approve_resolves_the_action(fake_telegram, client, session_id):
    action_id = await _make_held_action(session_id)
    client.post("/v1/telegram/webhook", json=_callback(action_id, "approve"))

    async with session_scope() as db:
        action = await approvals.get(db, action_id)
    assert action.status is ApprovalStatus.APPROVED


@pytest.mark.asyncio
async def test_a_second_tap_cannot_change_the_outcome(fake_telegram, client, session_id):
    action_id = await _make_held_action(session_id)
    client.post("/v1/telegram/webhook", json=_callback(action_id, "deny"))
    client.post("/v1/telegram/webhook", json=_callback(action_id, "approve"))

    async with session_scope() as db:
        action = await approvals.get(db, action_id)
    assert action.status is ApprovalStatus.DENIED, "first decision wins"


@pytest.mark.asyncio
async def test_a_callback_from_another_chat_authorises_nothing(
    fake_telegram, client, session_id
):
    action_id = await _make_held_action(session_id)
    response = client.post("/v1/telegram/webhook",
                           json=_callback(action_id, "approve", chat="99999"))
    assert response.status_code == 200

    async with session_scope() as db:
        action = await approvals.get(db, action_id)
    assert action.status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_a_forged_signature_authorises_nothing(fake_telegram, client, session_id):
    action_id = await _make_held_action(session_id)
    forged = f"{action_id}:approve:0000000000000000"
    client.post("/v1/telegram/webhook", json=_callback(action_id, "approve", data=forged))

    async with session_scope() as db:
        action = await approvals.get(db, action_id)
    assert action.status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_a_callback_for_another_action_does_not_cross_over(
    fake_telegram, client, session_id
):
    """A signature is bound to one action id and cannot be replayed onto another."""
    first = await _make_held_action(session_id)
    second = await _make_held_action(session_id)

    # Take the valid signature for `first` and aim it at `second`.
    stolen = sign_callback(first, "approve").split(":")[-1]
    client.post("/v1/telegram/webhook",
                json=_callback(second, "approve", data=f"{second}:approve:{stolen}"))

    async with session_scope() as db:
        assert (await approvals.get(db, second)).status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_the_wrong_webhook_secret_is_ignored(fake_telegram, client, session_id):
    action_id = await _make_held_action(session_id)
    response = client.post(
        "/v1/telegram/webhook",
        json=_callback(action_id, "approve"),
        headers={"X-Telegram-Bot-Api-Secret-Token": "not-the-secret"},
    )
    assert response.status_code == 200

    async with session_scope() as db:
        assert (await approvals.get(db, action_id)).status is ApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_the_right_webhook_secret_is_accepted(fake_telegram, client, session_id):
    action_id = await _make_held_action(session_id)
    secret = get_settings().signing_key[:32]
    client.post(
        "/v1/telegram/webhook",
        json=_callback(action_id, "deny"),
        headers={"X-Telegram-Bot-Api-Secret-Token": secret},
    )

    async with session_scope() as db:
        assert (await approvals.get(db, action_id)).status is ApprovalStatus.DENIED


# --------------------------------------------------------------------------
# the whole loop, as the agent experiences it
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_agent_blocks_until_the_human_taps(
    fake_telegram, client, session_id
):
    """The waiter blocks, then returns the human's decision — not a timeout.

    Driven directly rather than through a threaded HTTP client: mixing
    TestClient's own event loop with this test's produced a race where the card
    expired before the tap landed, which measured the timeout instead of the
    decision. The mechanism under test is `wait_for_decision`, and this
    exercises exactly that.
    """
    action_id = await _make_held_action(session_id, expires_in=60)
    async with session_scope() as db:
        action = await approvals.get(db, action_id)
        action.telegram_message_id = await telegram.push_card(action)

    assert fake_telegram.calls_to("sendMessage"), "no card was delivered"

    async def tap_deny_shortly():
        await asyncio.sleep(0.3)
        async with session_scope() as db:
            await approvals.resolve(db, action_id,
                                    decision=ApprovalStatus.DENIED,
                                    resolved_by="telegram:operator")

    waiter = asyncio.create_task(approvals.wait_for_decision(action_id))
    tapper = asyncio.create_task(tap_deny_shortly())

    status = await asyncio.wait_for(waiter, timeout=20)
    await tapper

    assert status is ApprovalStatus.DENIED, "the human's decision should win"
    assert status is not ApprovalStatus.EXPIRED, "this must not be a timeout"


@pytest.mark.asyncio
async def test_an_unanswered_card_times_out_to_denial(fake_telegram, session_id):
    """The other half of the same guarantee: silence is denial."""
    action_id = await _make_held_action(session_id)

    status = await asyncio.wait_for(
        approvals.wait_for_decision(action_id, timeout=0.4), timeout=10)

    assert status is ApprovalStatus.EXPIRED
    assert status is not ApprovalStatus.APPROVED

    async with session_scope() as db:
        assert (await approvals.get(db, action_id)).resolved_by == "timeout"
