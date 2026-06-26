#!/usr/bin/env python3
"""aspec_to_n8n.py -- deterministic translator from an ASPEC instance to an
importable n8n workflow JSON.

Scope: the seven adapters of the Ajora action catalogue. This script is a direct,
rule-based instantiation of the ASPEC -> n8n mapping described in the thesis
(Table tab:aspec-n8n). It does NOT reimplement n8n; it covers exactly the adapters
the pipeline can generate, which is the natural and defensible scope.

What is and is not derived from the ASPEC:
  * Automation logic (node types, operations, parameters, data flow, expression
    conventions) is derived entirely from the ASPEC via the mapping below.
  * Real resource and credential identifiers are NOT in the ASPEC (it abstracts
    them by design, DR4/DR6). They are resolved through an explicit `bindings.json`
    file -- the binding layer made reproducible. A production deployment would
    populate this from each platform's resource-listing API.
  * n8n's internal scaffolding (node ids, positions) is generated here. Node ids
    are derived deterministically from the ASPEC step ids (uuid5), so re-running
    the translator on the same ASPEC produces a byte-identical workflow.

Usage:
  python aspec_to_n8n.py <aspec.json> [--bindings bindings.json]
                         [--redirect-email you@example.com] [--out out.json]

The optional --redirect-email overrides every `sendTo` so that the delivered email
can be observed in a mailbox you control; the substitution is reported on stderr.
"""

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

# Fixed namespace so node ids are stable across runs (reproducibility).
_NS = uuid.uuid5(uuid.NAMESPACE_URL, "ajora-aspec-n8n")

# --- Adapter registry -------------------------------------------------------
# Each catalogue adapter registers one builder that maps its ASPEC
# `configured_parameters` to the n8n node `parameters`. To add an adapter, write a
# builder and decorate it with @adapter(...); nothing else in the engine changes.

ADAPTER_REGISTRY = {}


def adapter(adapter_id, node_type, type_version, node_name, has_webhook=False):
    def register(build_fn):
        ADAPTER_REGISTRY[adapter_id] = {
            "type": node_type,
            "typeVersion": type_version,
            "name": node_name,
            "has_webhook": has_webhook,
            "build": build_fn,
        }
        return build_fn
    return register

_KNOWN_POLL = {"everyMinute", "everyHour", "everyDay", "everyWeek", "everyMonth"}

# Per-trigger field-name maps: catalogue logical field -> n8n output field.
_FIELD_MAP = {
    "email_object": {"from": "From", "sender": "From", "subject": "Subject",
                     "body": "text", "snippet": "snippet"},
    "send_result":  {"messageId": "id"},
}


def _node_id(step_id):
    return str(uuid.uuid5(_NS, step_id))


def _webhook_id(step_id):
    return str(uuid.uuid5(_NS, step_id + "::webhook"))


def _map_poll(value):
    if value in _KNOWN_POLL:
        return value
    sys.stderr.write(
        f"[warn] pollTimes '{value}' is not a fixed n8n mode; defaulting to 'everyMinute'. "
        "The ASPEC did not capture an exact interval.\n")
    return "everyMinute"


def _translate_inner(expr):
    """Translate one catalogue-convention reference to an n8n-native one."""
    expr = expr.strip()
    # row_object.row['Col'] / ["Col"]  ->  $json['Col']
    m = re.match(r"^\w+\.row(\[.*\])$", expr)
    if m:
        return "$json" + m.group(1)
    if "." in expr:
        obj, rest = expr.split(".", 1)
        rest = _FIELD_MAP.get(obj, {}).get(rest, rest)
        return "$json." + rest
    return "$json"


def translate_value(text):
    """Translate a parameter value that may contain {{ ... }} references.

    Returns an n8n expression string (prefixed with '=') if any reference is
    present, otherwise the literal text unchanged.
    """
    if not isinstance(text, str) or "{{" not in text:
        return text
    out = re.sub(r"\{\{\s*(.*?)\s*\}\}", lambda m: "{{ " + _translate_inner(m.group(1)) + " }}", text)
    return "=" + out


def _resolve(mock_id, bindings, kind):
    table = bindings.get(kind, {})
    if mock_id not in table:
        raise SystemExit(
            f"[error] no binding for {kind[:-1]} '{mock_id}'. Add it to bindings.json "
            f"(maps the ASPEC's abstract id to a real id in your n8n instance).")
    return table[mock_id]


def _rl(res):
    """Resource-locator object for documentId / databaseId / folderToWatch."""
    return {"__rl": True, "value": res["id"], "mode": "list",
            "cachedResultName": res["name"], "cachedResultUrl": res.get("url", "")}


def _rl_sheet(sheet_name, doc):
    return {"__rl": True, "value": "gid=0", "mode": "list",
            "cachedResultName": sheet_name, "cachedResultUrl": doc.get("url", "") + "#gid=0"}


def _gmail_filters(filters):
    out = {}
    if not filters:
        return out
    if filters.get("sender"):
        out["sender"] = filters["sender"]
    if filters.get("from"):
        out["sender"] = filters["from"]
    q = []
    if filters.get("subject"):
        q.append(f"subject:{filters['subject']}")
    if filters.get("to"):
        q.append(f"to:{filters['to']}")
    if q:
        out["q"] = " ".join(q)
    return out


def _credentials(unit, aspec, bindings):
    ref = unit.get("credential_ref")
    if not ref:
        return None
    entry = aspec["credentials"][ref]
    real = _resolve(entry["credential_id"], bindings, "credentials")
    return {entry["auth_type"]: {"id": real["id"], "name": real["name"]}}


@adapter("email.trigger.gmail", "n8n-nodes-base.gmailTrigger", 1.3, "Gmail Trigger")
def _build_gmail_trigger(cp, ctx):
    return {"pollTimes": {"item": [{"mode": _map_poll(cp.get("pollTimes", "everyMinute"))}]},
            "filters": _gmail_filters(cp.get("filters", {}))}


@adapter("email.send.gmail", "n8n-nodes-base.gmail", 2.2, "Send Email", has_webhook=True)
def _build_gmail_send(cp, ctx):
    send_to = cp.get("sendTo", "")
    if ctx.redirect_email:
        sys.stderr.write(f"[info] redirecting sendTo '{send_to}' -> '{ctx.redirect_email}'.\n")
        send_to = ctx.redirect_email
    return {"sendTo": send_to,
            "subject": cp.get("subject", ""),
            "emailType": cp.get("emailType", "text"),
            "message": translate_value(cp.get("message", "")),
            "options": {}}


@adapter("email.label.gmail", "n8n-nodes-base.gmail", 2.2, "Add Label", has_webhook=True)
def _build_gmail_label(cp, ctx):
    return {"operation": "addLabels",
            "messageId": translate_value(cp.get("messageId", "")),
            "labelIds": cp.get("labelIds", [])}  # passed through verbatim


@adapter("spreadsheet.trigger.google_sheets", "n8n-nodes-base.googleSheetsTrigger", 1, "Google Sheets Trigger")
def _build_sheets_trigger(cp, ctx):
    doc = _resolve(cp["documentId"], ctx.bindings, "resources")
    return {"pollTimes": {"item": [{"mode": _map_poll(cp.get("pollTimes", "everyMinute"))}]},
            "documentId": _rl(doc),
            "sheetName": _rl_sheet(cp.get("sheetName", "Sheet1"), doc),
            "event": cp.get("event", "rowAdded"),
            "options": {}}


@adapter("spreadsheet.append.google_sheets", "n8n-nodes-base.googleSheets", 4.7, "Append to Sheet")
def _build_sheets_append(cp, ctx):
    doc = _resolve(cp["documentId"], ctx.bindings, "resources")
    cols = cp.get("columns", {})
    value = {k: translate_value(v) for k, v in cols.items()}
    schema = [{"id": k, "displayName": k, "required": False, "defaultMatch": False,
               "display": True, "type": "string", "canBeUsedToMatch": True, "removed": False}
              for k in cols]
    return {"operation": cp.get("operation", "append"),
            "documentId": _rl(doc),
            "sheetName": _rl_sheet(cp.get("sheetName", "Sheet1"), doc),
            "columns": {"mappingMode": "defineBelow", "value": value,
                        "matchingColumns": list(cols.keys())[:1], "schema": schema,
                        "attemptToConvertTypes": False,
                        "convertFieldsToString": cp.get("convertFieldsToString", False)},
            "options": {}}


@adapter("database.create_record.notion", "n8n-nodes-base.notion", 2.2, "Create Notion Page")
def _build_notion_create(cp, ctx):
    db = _resolve(cp["databaseId"], ctx.bindings, "resources")
    # Notion property types are not carried in the ASPEC; default to rich_text.
    prop_values = [{"key": f"{name}|rich_text", "rich_text": translate_value(expr)}
                   for name, expr in cp.get("propertiesUi", {}).items()]
    return {"resource": "databasePage",
            "databaseId": _rl(db),
            "title": translate_value(cp.get("title", "")),
            "propertiesUi": {"propertyValues": prop_values},
            "options": {}}


@adapter("file_storage.trigger.google_drive", "n8n-nodes-base.googleDriveTrigger", 1, "Google Drive Trigger")
def _build_drive_trigger(cp, ctx):
    folder = _resolve(cp["folderToWatch"], ctx.bindings, "resources")
    return {"pollTimes": {"item": [{"mode": _map_poll(cp.get("pollTimes", "everyMinute"))}]},
            "triggerOn": cp.get("triggerOn", "specificFolder"),
            "folderToWatch": _rl(folder),
            "event": cp.get("event", "fileCreated"),
            "options": {}}


def _build_node(unit, aspec, ctx, position, used_names):
    adapter_id = unit["adapter_id"]
    spec = ADAPTER_REGISTRY.get(adapter_id)
    if spec is None:
        raise SystemExit(
            f"[error] adapter '{adapter_id}' is outside the catalogue scope of this translator.")

    name = spec["name"]
    while name in used_names:
        name = f"{spec['name']} ({unit['step_id']})"
    used_names.add(name)

    node = {
        "parameters": spec["build"](unit.get("configured_parameters", {}), ctx),
        "type": spec["type"],
        "typeVersion": spec["typeVersion"],
        "position": position,
        "id": _node_id(unit["step_id"]),
        "name": name,
    }
    if spec["has_webhook"]:
        node["webhookId"] = _webhook_id(unit["step_id"])
    creds = _credentials(unit, aspec, ctx.bindings)
    if creds:
        node["credentials"] = creds
    return name, node


def translate(aspec, bindings, redirect_email=None):
    ctx = SimpleNamespace(bindings=bindings, redirect_email=redirect_email)
    units = [aspec["trigger"]] + aspec.get("steps", [])
    name_of = {}
    nodes = []
    used_names = set()
    for i, unit in enumerate(units):
        name, node = _build_node(unit, aspec, ctx, [220 * i, 0], used_names)
        name_of[unit["step_id"]] = name
        nodes.append(node)

    connections = {}
    for c in aspec.get("connections", []):
        src, tgt = name_of[c["from"]], name_of[c["to"]]
        connections.setdefault(src, {}).setdefault("main", [[]])
        connections[src]["main"][0].append({"node": tgt, "type": "main", "index": 0})

    return {
        "name": aspec.get("metadata", {}).get("name", "Translated ASPEC workflow"),
        "nodes": nodes,
        "pinData": {},
        "connections": connections,
        "active": False,
        "settings": {"executionOrder": "v1"},
        "tags": [],
    }


def main():
    ap = argparse.ArgumentParser(description="Translate an ASPEC instance into an importable n8n workflow.")
    ap.add_argument("aspec", help="path to the ASPEC JSON file")
    ap.add_argument("--bindings", default=str(Path(__file__).parent / "bindings.json"),
                    help="path to the mock-id -> real-id bindings file (default: bindings.json beside this script)")
    ap.add_argument("--redirect-email", default=None,
                    help="override every sendTo so delivery can be observed in a mailbox you control")
    ap.add_argument("--out", default=None, help="output path (default: stdout)")
    args = ap.parse_args()

    aspec = json.loads(Path(args.aspec).read_text())
    bindings = json.loads(Path(args.bindings).read_text())
    workflow = translate(aspec, bindings, args.redirect_email)
    text = json.dumps(workflow, indent=2, ensure_ascii=False)

    if args.out:
        Path(args.out).write_text(text + "\n")
        sys.stderr.write(f"[ok] wrote {args.out}\n")
    else:
        print(text)


if __name__ == "__main__":
    main()
