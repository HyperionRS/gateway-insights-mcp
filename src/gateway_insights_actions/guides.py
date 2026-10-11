# SPDX-License-Identifier: Apache-2.0
"""How to fill each batch operation: written from running every one of them against a console.

batch_fields shows a type's guide next to its form. A guide says how to pick the objects, what the
payload takes and in which format, the CSV header, which dictionaries hold the values, what the
refusal codes mean, and the way back. The console stays the authority: a guide explains, the preview
decides.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class Guide:
    objects: str
    payload: str = ""
    csv: str = ""
    dictionaries: str = ""
    refusals: Tuple[Tuple[str, str], ...] = ()
    way_back: str = ""
    notes: Tuple[str, ...] = field(default_factory=tuple)


ORDERS = ("Order ids from order_search (ids), or a CSV in the header below. A batch over orders takes no "
          "filter: search first, then pass order_ids.")
ORDER_REFUSALS = (("object.not.accessible", "no such order, or not one this token may see"),
                  ("order.action.failed", "the processing refused the operation for this order"),
                  ("unsupported.transaction.type.transition", "the order's last transaction does not allow it"))
MONEY_BACK = "None by batch: a separate order operation, decided by a person."

GUIDES: Dict[str, Guide] = {
    # ---- orders -------------------------------------------------------------------------------
    "ORDER_QUERY_STATUS": Guide(
        ORDERS + " Typical: orders in UNKNOWN, ERROR or PROCESSING.", "comment (free text).",
        "order_id;comment", refusals=ORDER_REFUSALS,
        way_back="Nothing to undo: it asks the provider again.",
        notes=("plan_status_check takes order_search arguments and puts every order found into the task.",)),
    "ORDER_CHARGEBACK": Guide(
        ORDERS + " Approved sales or captures.",
        "date (YYYY-MM-DD), reason (a code exactly as batch_dictionaries chargeback_reasons lists it, e.g. 10,4), amount, "
        "comment.", "order_id;date;reason_code;amount;comment", "batch_dictionaries chargeback_reasons",
        ORDER_REFUSALS, MONEY_BACK),
    "ORDER_FRAUD": Guide(ORDERS + " Approved orders.", "date (YYYY-MM-DD), reason (fraud_reasons), comment.",
                         "order_id;date;reason_code;comment", "batch_dictionaries fraud_reasons", ORDER_REFUSALS,
                         MONEY_BACK),
    "ORDER_RETRIEVAL": Guide(ORDERS + " Approved orders.", "comment.", "order_id;comment",
                             refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_REVERSAL": Guide(ORDERS + " Approved sales.", "amount (empty — the full amount), comment.",
                            "order_id;amount;comment", "batch_dictionaries reversal_reasons", ORDER_REFUSALS,
                            MONEY_BACK),
    "ORDER_CAPTURE": Guide(ORDERS + " Approved authorisations (preauth).", "amount.", "order_id;amount",
                           refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_RESOLVE_UNKNOWN": Guide(
        ORDERS + " Orders in UNKNOWN.", "status (APPROVED or DECLINED), ext (the provider's order id, optional).",
        "order_id;status;provider_order_id", refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_FAUX_REVERSAL": Guide(
        ORDERS + " Approved orders.",
        "datetime (UTC, YYYY-MM-DDTHH:MM, not before the original transaction), reason (faux_reversal_reasons), "
        "amount.", "order_id;datetime_utc;reason;amount", "batch_dictionaries faux_reversal_reasons",
        ORDER_REFUSALS + (("date.before.original.transaction", "the date is before the order's transaction"),),
        MONEY_BACK),
    "ORDER_VOID": Guide(ORDERS + " Approved sales.", "comment.", refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_CANCEL": Guide(ORDERS + " Approved authorisations.", "comment.", refusals=ORDER_REFUSALS,
                          way_back=MONEY_BACK),
    "ORDER_PAYOUT_CANCEL": Guide(ORDERS + " Approved payouts.", "amount, comment.", refusals=ORDER_REFUSALS,
                                 way_back=MONEY_BACK),
    "ORDER_CHARGEBACK_REVERSAL": Guide(ORDERS + " Orders with a chargeback.", "amount, comment.",
                                       refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_PREARBITRATION": Guide(ORDERS + " Orders with a chargeback.", "amount, comment.",
                                  refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_ARBITRATION": Guide(ORDERS + " Orders after pre-arbitration.", "amount, comment.",
                               refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_APPROVE": Guide(ORDERS + " Declined or failed orders: rolls the last transaction back and resolves it "
                           "approved.", refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_DECLINE": Guide(ORDERS + " Approved orders: rolls the last transaction back and resolves it declined.",
                           refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_ROLLBACK_AND_CHECK": Guide(ORDERS + " Rolls the last transaction back and asks the provider again.",
                                      refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_CHANGE_PROVIDER_ORDER_ID": Guide("CSV only: one line per order.", "",
                                            "order_id;status;provider_order_id", refusals=ORDER_REFUSALS,
                                            way_back="The same type with the earlier id."),
    "ORDER_CHANGE_DATE": Guide(ORDERS + " Approved orders.", "datetime (UTC, YYYY-MM-DDTHH:MM).",
                               "order_id;datetime_utc", refusals=ORDER_REFUSALS, way_back=MONEY_BACK),
    "ORDER_ROLLBACK_TO_STATE": Guide(ORDERS, way_back=MONEY_BACK, notes=("Disabled in the registry today.",)),
    # ---- bank terminals and routing ----------------------------------------------------------
    "BANK_TERMINAL_CLONE_REPLACE": Guide(
        "Terminals by ids, a selection (level BANK_TERMINAL), or CSV. The terminal must be in routing.",
        "provider (id; empty — as the original), name (template: {name} {id} {provider} {currency} {task} {n} "
        "{date}), descriptor, inherit_descriptor (Y/N), enabled (ENABLED by default: the copy takes the "
        "original's place in routing).",
        "bank_terminal_id;provider_id;name;descriptor — extra columns are the copy's credentials (class 3)",
        refusals=(("bank_terminal.not.in.routing", "the terminal is in no route: nothing to replace"),
                  ("property.not.defined", "a credential column the provider does not have")),
        way_back="Switch the copy out of routing and the original back in (plan_route_status; switching in is "
                 "run in the console). A created terminal is not deleted by a batch."),
    "BANK_TERMINAL_CLONE_EXTEND": Guide(
        "Terminals by ids, a selection, or CSV. The terminal must be in routing.",
        "As for replacing; enabled is DISABLED by default — the copy joins the pool with its routing rows off "
        "(class 1). ENABLED makes it class 2.",
        "bank_terminal_id;provider_id;name;descriptor (+ credential columns: class 3)",
        refusals=(("bank_terminal.not.in.routing", "the terminal is in no route"),
                  ("property.not.defined", "a credential column the provider does not have")),
        way_back="The copy stays, with its rows off; a batch does not delete a terminal."),
    "BANK_TERMINAL_ROUTING_REPLACE": Guide(
        "Terminals by ids, a selection, or CSV.", "new_bank_terminal (id of an existing terminal), enabled.",
        "bank_terminal_id;new_bank_terminal_id",
        refusals=(("bank_terminal.not.in.routing", "the terminal is in no route"),),
        way_back="The same type the other way round, run in the console."),
    "BANK_TERMINAL_ROUTING_STATUS": Guide(
        "Terminals by ids (plan_route_status), a selection, or CSV.", "row_status: ENABLED or DISABLED.",
        "bank_terminal_id;status",
        refusals=(("bank_terminal.not.in.routing", "the terminal is in no route"),
                  ("value.unchanged", "already in that state")),
        way_back="plan_revert. Switching out is class 1; switching back in is class 2 — run in the console.",
        notes=("The console reports «routing: enabled → disabled».",)),
    "BANK_TERMINAL_PROPERTY": Guide(
        "Terminals of one provider: a selection with provider_ids, or ids. Read batch_fields with provider_id "
        "first — the properties depend on the provider.",
        'changes: a JSON list [{"param": "<property key>", "value": "<value>"}].',
        refusals=(("value.unchanged", "already that value"),
                  ("property.not.batchable", "changed on the terminal's card only")),
        way_back="plan_revert (grouped by earlier values; an inherited value is listed, not set back).",
        notes=("A credential property makes the task class 3; its values are masked in every answer.",)),
    "BANK_TERMINAL_PROPERTY_CSV": Guide(
        "CSV only.", "", "bank_terminal_id;<property> — e.g. bank_terminal_id;terminal_location",
        refusals=(("value.unchanged", "already that value"),), way_back="plan_revert."),
    "BANK_TERMINAL_PSP_SECRET": Guide(
        "Terminals by ids (plan_secret) or a selection.",
        "param (the terminal property holding the password); the password itself only as secret_ref "
        "(source:name), never as text.",
        refusals=(("property.not.sensitive", "the property is not marked secret at the provider: use a property "
                   "change instead"), ("property.not.defined", "the provider has no such property")),
        way_back="None: the earlier password is not readable. Class 3 — run in the console."),
    "ROUTING_CRITERION_ADD_VALUE": Guide(
        "Blueprints by ids, a selection (level BLUEPRINT), or CSV.",
        "criterion (e.g. TRANSACTION_TYPE), value (a value of the rule to extend), new_value (the value to add). "
        "Values are ids: read them with blueprint_routing — the number in brackets (sale = 1, transfer = 11).",
        "blueprint_id;criterion;value;new_value",
        refusals=(("routing.value.not.found", "no route of the blueprint has that value"),
                  ("routing.new_value.in.other.route", "the new value already belongs to another route")),
        way_back="None by batch: no type removes a value — class 2, run in the console."),
    "ROUTING_CRITERION_REPLACE_VALUE": Guide(
        "Blueprints by ids, a selection, or CSV.",
        "criterion, value (the value to replace), new_value — ids as blueprint_routing shows them.",
        "blueprint_id;criterion;value;new_value",
        refusals=(("routing.value.not.found", "no route of the blueprint has that value"),),
        way_back="plan_revert (replaces it back)."),
    # ---- any level ----------------------------------------------------------------------------
    "RESTRICTION_PARAM": Guide(
        "Entities as LEVEL:id (BLUEPRINT:9; levels BLUEPRINT, REQUESTOR, BANK_TERMINAL, PROVIDER), a selection, "
        "or CSV.",
        "restriction (id), param (parameter id), value, old (expected current value, optional), "
        "attach: skip (default) or create_disabled — what to do where the restriction is not attached.",
        "level;entity_id;restriction_id;parameter;old_value;new_value",
        refusals=(("restriction.not.attached", "not attached and attach=skip"),
                  ("value.mismatch", "the current value is not the expected old one"),
                  ("value.unchanged", "already that value"),
                  ("restriction.excluded", "a parameter batches do not change (amount intervals, BIN ranges)")),
        way_back="plan_revert for the parameter; an attachment made by create_disabled is not undone by a batch.",
        notes=("Read the entity's restrictions with entity_restrictions: the list, then one restriction for its "
               "parameter ids and current values. The catalogue changes often — always read it, never assume.",)),
    "ENTITY_NAME": Guide(
        "Entities as LEVEL:id (BANK_TERMINAL, BLUEPRINT, REQUESTOR), a selection, or CSV.",
        "field (name or description; a requestor has name only), find, replace — literal, case-sensitive, every "
        "occurrence. Setting a whole new value is CSV.",
        "level;entity_id;field;new_value;old_value — old_value must equal the current one",
        refusals=(("stale.value", "old_value is not the current value: read it with entity_search first"),
                  ("sub_key.invalid", "a field the level does not have"),
                  ("value.unchanged", "nothing to replace")),
        way_back="plan_revert."),
    "ENTITY_RATE_PLAN": Guide(
        "Entities as LEVEL:id (BLUEPRINT, REQUESTOR, BANK_TERMINAL), a selection, or CSV.",
        "level, field (mrtp — manager tariff, rrtp — reseller tariff), value (a tariff id; inherit for a "
        "requestor), old (optional).",
        "level;entity_id;field;new_value;old_value",
        "the entity's manager: entity_search entity_type=MANAGER with blueprint_ids / requestor_ids / "
        "bank_terminal_ids=[…]; then batch_dictionaries manager_rate_plans manager_id=… (reseller the same way) — a "
        "tariff belongs to the entity's manager or reseller",
        (("rate.plan.manager.mismatch", "the tariff is not of the entity's manager"),
         ("rate.plan.reseller.mismatch", "the tariff is not of the entity's reseller"),
         ("reseller.absent", "the entity has no reseller"),
         ("rate.plan.disabled", "the tariff is switched off")),
        "plan_revert."),
    "ENTITY_ENABLE": Guide(
        "Requestors by ids (plan_disable) or a selection (level REQUESTOR).", "value: ENABLED or DISABLED.",
        refusals=(("value.unchanged", "already in that state"),),
        way_back="plan_revert.", notes=("The console reports «status_name: E → D».",)),
    "REQUESTOR_CONFIG": Guide(
        "Requestors by ids or a selection.",
        'changes: [{"param": "<key>", "value": "<value>"}] with keys from batch_fields (auto capture and return, '
        "card holder, address, e-mail and SBP generation…).",
        refusals=(("attribute.not.config", "not a config field (some are changed on the card only)"),
                  ("value.unchanged", "already that value")),
        way_back="plan_revert (an inherited value is listed, not set back)."),
    "REQUESTOR_EAV": Guide(
        "Requestors by ids, a selection, or CSV.",
        'changes: [{"param": "<attribute>", "value": "<value>"}] — e.g. payment_form_layout_mode, '
        "redirect_restriction_mode.", "requestor_id;<attribute> — e.g. requestor_id;redirect_restriction_mode",
        refusals=(("attribute.unknown", "the requestor does not have the attribute yet: create it on the card"),
                  ("value.unchanged", "already that value")),
        way_back="plan_revert."),
    "REQUESTOR_API_HOST": Guide(
        "Requestors by ids, a selection, or CSV (plan_api_host).",
        "from_hosts (comma-separated; must include the host the tokens use now), to_host (an API host of the "
        "requestor's manager: entity_search entity_type=MANAGER requestor_ids=[…], then api_hosts manager_id=…).",
        "requestor_id;from_hosts;to_host",
        refusals=(("api_host.not.allowed", "the new host is not an API host of the requestor's manager"),
                  ("api_host.not.matched", "no token uses any of from_hosts now")),
        way_back="plan_revert."),
    "REQUESTOR_ATTRIBUTE": Guide("Requestors by ids or a selection.", "attr, value.",
                                 notes=("Disabled in the registry today.",)),
    "MERCHANT_GENERATOR": Guide(
        'A merchant goes with its manager: objects=[{"id": <merchant id>, "sub_key": "<manager id>"}]. A selection '
        "does not carry the manager, so list the manager's merchants with entity_search (entity_type MERCHANT, "
        "manager_ids=[…]) and pass them as objects.",
        'changes: [{"param": "<key>", "value": "<value>"}]. Keys, for the customer and the same for the receiver '
        "(generator_receiver_…): generator_enabled_status (ENABLED/DISABLED); "
        "generator_customer_email_generation_enabled_status, …_address_…, …_dob_… (ENABLED/DISABLED/FORCED); "
        "generator_customer_name_generation_enabled_status (… or VALIDATE_AND_REPLACE); "
        "generator_customer_phone_generation_enabled_status (PASS_ORIGINAL/GENERATE_IF_EMPTY/FORCE); "
        "generator_customer_address_generation_default_country_code (ISO code); "
        "generator_customer_address_generation_romanization_enabled (ENABLED/DISABLED); "
        "generator_customer_identity_mode (LEGACY/NAME_AWARE). The console reports them stored as D/F/E.",
        refusals=(("payload.manager.required", "no manager given for the merchant"),
                  ("object.id.invalid", "the id is not a merchant id (put the manager into sub_key, not the id)"),
                  ("merchant.manager.not.related", "the merchant is not under that manager")),
        way_back="plan_revert.", notes=("The change applies to every requestor of the merchant under that manager.",)),
}


def guide_text(task_type: str) -> str:
    g = GUIDES.get(task_type)
    if g is None:
        return ""
    lines = ["**How to fill it**", f"- Objects: {g.objects}"]
    if g.payload:
        lines.append(f"- Payload: {g.payload}")
    if g.csv:
        lines.append(f"- CSV: `{g.csv}`")
    if g.dictionaries:
        lines.append(f"- Values from: {g.dictionaries}")
    if g.refusals:
        lines.append("- Refusals: " + "; ".join(f"`{code}` — {why}" for code, why in g.refusals))
    if g.way_back:
        lines.append(f"- Way back: {g.way_back}")
    lines += [f"- {n}" for n in g.notes]
    return "\n".join(lines)


def missing(types: List[str]) -> List[str]:
    return [t for t in types if t not in GUIDES]
