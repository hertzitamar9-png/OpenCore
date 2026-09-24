"""Windows control picking for OpenCore Reflex: one request shape for training and live use.

State = window title + visible text; questions = which operation, then which control.
Candidates come from OpenCore's desktop inspect rows (elementId, name, controlType).
"""
from __future__ import annotations

import re

ACTIONABLE = {"Button", "SplitButton", "MenuItem", "Edit", "ComboBox", "CheckBox", "RadioButton",
              "TabItem", "ListItem", "TreeItem", "Hyperlink", "Slider", "Document"}
BIDI = re.compile("[‎‏‪-‮⁦-⁩]")
OPERATIONS = {
    "CLICK": "Click a button, menu item, tab, list item, link or checkbox.",
    "TYPE_TEXT": "Type or replace text in an editable field.",
    "SELECT": "Choose a value in a dropdown.",
    "DONE": "The request is already visibly done.",
    "BLOCKED": "No control in this window can do it.",
}
RULES = "Pick exactly the control that performs the request. Labels may be in any language."
MAX_CANDIDATES = 60


def clean(text):
    return BIDI.sub("", text or "").strip()


def operation_for(control_type):
    return "TYPE_TEXT" if control_type in ("Edit", "Document") else "SELECT" if control_type == "ComboBox" else "CLICK"


def candidates(elements, limit=MAX_CANDIDATES, keep=None):
    """Actionable rows as {elementId: label}; keeps `keep` if the table must be trimmed."""
    rows = [(e["elementId"], "%s (%s)" % (clean(e.get("name"))[:60], e.get("controlType")))
            for e in elements if e.get("controlType") in ACTIONABLE and clean(e.get("name")) and e.get("enabled", True)]
    seen, unique = set(), []
    for element_id, label in rows:
        if label not in seen:
            seen.add(label)
            unique.append((element_id, label))
    if len(unique) > limit:
        head = unique[:limit - 1]
        if keep is not None and all(i != keep for i, _ in head):
            head = head + [next(row for row in unique if row[0] == keep)]
        else:
            head = unique[:limit]
        unique = head
    return {str(element_id): "[%s] %s" % (element_id, label) for element_id, label in unique}


def state(title, elements, recent=()):
    text = " | ".join(clean(e.get("name"))[:80] for e in elements
                      if e.get("controlType") in ("Text", "Pane", "Group", "Window", "TitleBar", "StatusBar")
                      and clean(e.get("name")))[:1200]
    return {"window": {"title": clean(title), "text": text}, "recent_actions": list(recent)[-6:]}


def questions(goal, table):
    ops = {"operation": {"type": "choice", "criteria": OPERATIONS,
                         "instructions": {"goal": goal, "rules": RULES}}}
    if table:
        ops["target"] = {"type": "choice", "criteria": table,
                         "instructions": {"goal": goal, "rules": RULES + " Choose the control to act on."}}
    return ops
