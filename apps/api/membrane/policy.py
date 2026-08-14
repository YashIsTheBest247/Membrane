"""Capability policy: the map from tool names to capabilities and sensitivity.

Enumerating what is permitted terminates; enumerating what is forbidden does
not. So this file is an allow-list of capabilities, and any tool that does not
map onto one is held rather than passed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .models import Sensitivity

DEFAULT_POLICY: dict[str, Any] = {
    "version": 1,
    # Capabilities the operator can grant in an intent contract.
    "capabilities": {
        "web.fetch": {"sensitivity": "read", "description": "Retrieve a URL"},
        "web.search": {"sensitivity": "read", "description": "Run a search query"},
        "docs.read": {"sensitivity": "read", "description": "Read a document or file"},
        "mail.read": {"sensitivity": "read", "description": "Read mailbox contents"},
        "calendar.read": {"sensitivity": "read", "description": "Read calendar"},
        "crm.read": {"sensitivity": "read", "description": "Read CRM records"},
        "db.read": {"sensitivity": "read", "description": "Run a read query"},
        "memory.write": {"sensitivity": "write", "description": "Persist to agent memory"},
        "docs.write": {"sensitivity": "write", "description": "Create or edit a document"},
        "crm.write": {"sensitivity": "write", "description": "Update CRM records"},
        "calendar.write": {"sensitivity": "write", "description": "Create or edit events"},
        "http.post": {"sensitivity": "write", "description": "POST to an endpoint"},
        "mail.send": {"sensitivity": "irreversible", "description": "Send mail as the user"},
        "chat.post": {"sensitivity": "irreversible", "description": "Post to a channel"},
        "payment.transfer": {"sensitivity": "irreversible", "description": "Move money"},
        "file.write": {"sensitivity": "irreversible", "description": "Write to the filesystem"},
        "file.delete": {"sensitivity": "irreversible", "description": "Delete data"},
        "db.write": {"sensitivity": "irreversible", "description": "Mutate the database"},
        "shell.exec": {"sensitivity": "irreversible", "description": "Execute a command"},
        "iam.grant": {"sensitivity": "irreversible", "description": "Change permissions"},
    },
    # Tool-name patterns, evaluated in order. First match wins.
    "tools": [
        {"match": r"^(web|browser|browse|http)[._](get|fetch|open|read|visit)$", "capability": "web.fetch"},
        {"match": r"^(fetch_url|fetch|open_url|read_url|get_page|scrape)$", "capability": "web.fetch"},
        {"match": r"^(web|google|bing|duckduckgo)[._]?search$|^search(_web)?$", "capability": "web.search"},
        {"match": r"^(docs?|file|fs|drive|sharepoint)[._](read|get|open|cat|list)$", "capability": "docs.read"},
        {"match": r"^read_(file|document|pdf)$", "capability": "docs.read"},
        {"match": r"^(mail|gmail|outlook|imap)[._](read|list|search|get)$", "capability": "mail.read"},
        {"match": r"^(read|list|search)_(mail|email|inbox)$", "capability": "mail.read"},
        {"match": r"^calendar[._](read|list|get)$", "capability": "calendar.read"},
        {"match": r"^(crm|salesforce|hubspot)[._](read|get|list|query)$", "capability": "crm.read"},
        {"match": r"^(db|sql|database)[._](read|query|select)$", "capability": "db.read"},
        {"match": r"^memory[._](write|store|save|remember|append)$", "capability": "memory.write"},
        {"match": r"^(docs?|drive|notion|wiki)[._](write|create|update|edit|append)$", "capability": "docs.write"},
        {"match": r"^(crm|salesforce|hubspot)[._](write|update|create)$", "capability": "crm.write"},
        {"match": r"^calendar[._](write|create|update|invite)$", "capability": "calendar.write"},
        {"match": r"^(http|api|webhook)[._](post|put|patch|request)$", "capability": "http.post"},
        {"match": r"^(mail|gmail|outlook|smtp|email)[._](send|reply|forward|draft_send)$", "capability": "mail.send"},
        {"match": r"^send_(mail|email|message)$", "capability": "mail.send"},
        {"match": r"^(slack|teams|discord|chat)[._](post|send|message)$", "capability": "chat.post"},
        {"match": r"^(payment|billing|bank|stripe)[._](transfer|charge|pay|payout)$", "capability": "payment.transfer"},
        {"match": r"^wire_transfer$|^make_payment$|^transfer_funds$", "capability": "payment.transfer"},
        {"match": r"^(file|fs)[._](write|create|save|move|copy)$", "capability": "file.write"},
        {"match": r"^(file|fs|storage)[._](delete|remove|unlink|rm)$", "capability": "file.delete"},
        {"match": r"^(db|sql|database)[._](write|insert|update|delete|drop|execute)$", "capability": "db.write"},
        {"match": r"^(shell|bash|sh|terminal|exec|subprocess)[._]?(exec|run|command)?$", "capability": "shell.exec"},
        {"match": r"^(iam|acl|permissions?)[._](grant|add|allow|share)$", "capability": "iam.grant"},
    ],
    # Argument names whose values are treated as an egress destination. A
    # tainted value here is the confused-deputy signature.
    "destination_args": [
        "to", "recipient", "recipients", "cc", "bcc", "url", "endpoint",
        "webhook", "channel", "address", "iban", "account", "account_number",
        "destination", "target", "path", "host", "email", "phone", "beneficiary",
    ],
    # Read capabilities never require a human even when arguments are tainted;
    # everything else does when taint is present.
    "always_human": ["payment.transfer", "mail.send", "file.delete", "shell.exec",
                     "iam.grant", "db.write", "file.write", "chat.post"],
}


@dataclass
class ToolBinding:
    tool: str
    capability: str | None
    sensitivity: Sensitivity
    matched_rule: str | None = None

    @property
    def known(self) -> bool:
        return self.capability is not None


@dataclass
class Policy:
    data: dict[str, Any] = field(default_factory=lambda: DEFAULT_POLICY)
    _compiled: list[tuple[re.Pattern[str], dict]] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self._compiled = [
            (re.compile(rule["match"], re.I), rule) for rule in self.data.get("tools", [])
        ]

    # -- loading ---------------------------------------------------------

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Policy":
        """Load a policy file, falling back to the built-in default."""
        if path is None:
            return cls()
        file_path = Path(path)
        if not file_path.exists():
            return cls()
        try:
            import yaml  # type: ignore

            loaded = yaml.safe_load(file_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return cls()
        merged = {**DEFAULT_POLICY, **loaded}
        # Tool rules from the file take precedence, then the defaults, so an
        # operator can add tools without restating the whole catalogue.
        merged["tools"] = list(loaded.get("tools", [])) + list(DEFAULT_POLICY["tools"])
        merged["capabilities"] = {**DEFAULT_POLICY["capabilities"],
                                  **loaded.get("capabilities", {})}
        return cls(data=merged)

    # -- queries ---------------------------------------------------------

    def bind(self, tool: str) -> ToolBinding:
        """Map a tool name onto a capability and its sensitivity."""
        name = (tool or "").strip()
        for pattern, rule in self._compiled:
            if pattern.match(name):
                capability = rule["capability"]
                sensitivity = rule.get("sensitivity") or self.data["capabilities"].get(
                    capability, {}
                ).get("sensitivity", "write")
                return ToolBinding(name, capability, Sensitivity(sensitivity), rule["match"])

        # An explicitly declared capability name used directly as a tool name.
        if name in self.data["capabilities"]:
            sensitivity = self.data["capabilities"][name].get("sensitivity", "write")
            return ToolBinding(name, name, Sensitivity(sensitivity), "direct")

        return ToolBinding(name, None, Sensitivity.IRREVERSIBLE, None)

    def sensitivity_of(self, capability: str) -> Sensitivity:
        entry = self.data["capabilities"].get(capability)
        if not entry:
            return Sensitivity.IRREVERSIBLE
        return Sensitivity(entry.get("sensitivity", "write"))

    def is_destination_arg(self, arg_path: str) -> bool:
        leaf = arg_path.split(".")[-1].split("[")[0].lower()
        return leaf in {a.lower() for a in self.data.get("destination_args", [])}

    def always_human(self, capability: str) -> bool:
        return capability in set(self.data.get("always_human", []))

    @property
    def capability_names(self) -> list[str]:
        return sorted(self.data["capabilities"].keys())

    def catalogue(self) -> list[dict]:
        return [
            {"capability": name, **meta}
            for name, meta in sorted(self.data["capabilities"].items())
        ]


_policy: Policy | None = None


def get_policy() -> Policy:
    global _policy
    if _policy is None:
        import os

        _policy = Policy.load(os.environ.get("MEMBRANE_POLICY_FILE"))
    return _policy


def set_policy(policy: Policy) -> None:
    global _policy
    _policy = policy
