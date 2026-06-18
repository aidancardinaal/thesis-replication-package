from __future__ import annotations

from contextlib import contextmanager

import streamlit as st
from classes import Resource, ResourceType
from ajora_automation_reasoning_agent_v4 import AjoraAgent, _save_aspec_to_file
from llm_safety import ContentPolicyError


@contextmanager
def content_guard():
    """Catch provider content-filter rejections, checkpoint, and let the user retry.

    The agent lives in st.session_state, so state is not advanced on failure —
    the participant simply re-submits (in-session resume).
    """
    try:
        yield
    except ContentPolicyError as cpe:
        report_content_block(cpe, st.session_state.get("agent"))
        st.stop()

st.set_page_config(page_title="ASPEC Pipeline", layout="centered")
st.title("ASPEC Pipeline")
st.caption("Describe your automation in plain language")

# ── Session state init ────────────────────────────────────────────────────────

if "agent" not in st.session_state:
    st.session_state.agent = None
if "stage" not in st.session_state:
    st.session_state.stage = "idle"
if "scenario" not in st.session_state:
    st.session_state.scenario = None
if "aspec_saved" not in st.session_state:
    st.session_state.aspec_saved = False
if "resource_idx" not in st.session_state:
    st.session_state.resource_idx = 0
# Dialogue turn state (shared across clarifying + parameter stages)
if "current_question" not in st.session_state:
    st.session_state.current_question = None   # evolves on follow-ups
if "original_question" not in st.session_state:
    st.session_state.original_question = None  # fixed for refine call
if "follow_up_response" not in st.session_state:
    st.session_state.follow_up_response = None # LLM reply to show above input
if "turn_count" not in st.session_state:
    st.session_state.turn_count = 0            # ensures fresh input after each submit


def get_agent() -> AjoraAgent:
    return st.session_state.agent


def sync_stage():
    st.session_state.stage = get_agent().automation.status
    st.session_state.resource_idx = 0
    st.session_state.current_question = None
    st.session_state.original_question = None
    st.session_state.follow_up_response = None


def ask(label: str, form_key: str, value: str = ""):
    """Render a question label + auto-growing text area. Returns (submitted, answer)."""
    with st.form(form_key):
        answer = st.text_area(label, value=value, height="content")
        submitted = st.form_submit_button("→", type="primary")
    return submitted, answer


def report_content_block(cpe: ContentPolicyError, agent) -> str | None:
    """Checkpoint, log, and surface a content-filter rejection. Returns a checkpoint path."""
    ckpt = agent.save_checkpoint() if agent else None
    # Log the underlying error to the terminal — `str(cpe)` is the original
    # exception text that triggered the content-filter classification, useful
    # for telling a real provider block apart from a misclassification.
    print(f"\n⚠️  ContentPolicyError raised — underlying error: {cpe}")
    st.warning("⚠️ The content filter rejected that input. Your progress is saved — please rephrase and try again.")
    if ckpt:
        st.caption("Checkpoint:")
        st.code(ckpt, language=None)
    with st.expander("Details (for diagnosis)"):
        st.code(str(cpe))
    return ckpt


# ── idle ──────────────────────────────────────────────────────────────────────

if st.session_state.stage == "idle":
    scenario_options = {
        "1 — Log incoming emails to spreadsheet": "1",
        "2 — Filter customer emails → Notion + colleague notification": "2",
        "3 — Expense added to sheet → email finance + label email": "3",
        "4 — New file in Drive folder → email finance": "4",
        "0 — Other / free run": "other",
    }
    selected_label = st.selectbox("Which scenario is this run for?", list(scenario_options.keys()))
    st.session_state.scenario = scenario_options[selected_label]

    submitted, prompt = ask("What should your automation do?", "prompt_form")
    if submitted and prompt.strip():
        agent = AjoraAgent()
        agent.scenario = st.session_state.scenario
        agent.automation.description_of_automation = prompt.strip()
        st.session_state.agent = agent
        st.session_state.stage = "clarifying"
        st.session_state.aspec_saved = False
        st.rerun()

# ── clarifying ────────────────────────────────────────────────────────────────

elif st.session_state.stage == "clarifying":
    agent = get_agent()

    if not agent.pending_clarification_question:
        # Reset dialogue state before fetching next question
        st.session_state.current_question = None
        st.session_state.original_question = None
        st.session_state.follow_up_response = None
        try:
            with st.spinner("Thinking…"):
                agent.check_status()
        except ContentPolicyError as cpe:
            # This call sends `automation.description_of_automation` itself —
            # there's no pending question to re-ask, so the only retry surface
            # is letting the participant rewrite the description that got flagged.
            report_content_block(cpe, agent)
            st.write("Try rephrasing your automation description below and resubmit:")
            submitted, new_desc = ask(
                "Automation description",
                f"rephrase_description_{st.session_state.turn_count}",
                value=agent.automation.description_of_automation,
            )
            if submitted and new_desc.strip():
                agent.automation.description_of_automation = new_desc.strip()
                st.session_state.turn_count += 1
                st.rerun()
            st.stop()
        if agent.automation.status != "clarifying":
            sync_stage()
        st.rerun()

    # Initialise on first render of this question
    if st.session_state.current_question is None:
        st.session_state.current_question = agent.pending_clarification_question
        st.session_state.original_question = agent.pending_clarification_question

    if st.session_state.follow_up_response:
        st.write(st.session_state.follow_up_response)

    form_key = f"clarify_{st.session_state.turn_count}"
    submitted, answer = ask(st.session_state.current_question, form_key)
    if submitted and answer.strip():
        context = f"Automation so far: {agent.automation.description_of_automation}"
        with content_guard(), st.spinner("Processing…"):
            result = agent._classify_dialogue_turn(
                st.session_state.current_question, answer.strip(), context
            )
        st.session_state.turn_count += 1
        if result.is_answer:
            with content_guard(), st.spinner("Updating…"):
                agent.refine_automation_description_with_clarification(
                    st.session_state.original_question, answer.strip()
                )
                agent.pending_clarification_question = None
            st.session_state.current_question = None
            st.session_state.original_question = None
            st.session_state.follow_up_response = None
        else:
            st.session_state.follow_up_response = result.follow_up_response
            st.session_state.current_question = (
                result.follow_up_question or st.session_state.current_question
            )
        st.rerun()

# ── configuring_tools ─────────────────────────────────────────────────────────

elif st.session_state.stage == "configuring_tools":
    with content_guard(), st.spinner("Selecting tools for your automation…"):
        get_agent().check_status()
    sync_stage()
    st.rerun()

# ── configuring_credentials ───────────────────────────────────────────────────

elif st.session_state.stage == "configuring_credentials":
    agent = get_agent()
    if not agent.pending_missing_credentials:
        agent.check_status()
    agent.pending_missing_credentials = None
    sync_stage()
    st.rerun()

# ── configuring_resources ─────────────────────────────────────────────────────

elif st.session_state.stage == "configuring_resources":
    agent = get_agent()

    if not agent.pending_missing_resources and not agent.pending_resource_selection:
        agent.check_status()

    if agent.pending_missing_resources:
        agent.pending_missing_resources = None
        agent.automation.status = "configuring_parameters"
        sync_stage()
        st.rerun()

    elif agent.pending_resource_selection:
        idx = st.session_state.resource_idx
        if idx >= len(agent.pending_resource_selection):
            agent.pending_resource_selection = None
            agent.automation.status = "configuring_parameters"
            sync_stage()
            st.rerun()

        item = agent.pending_resource_selection[idx]
        options = [r["fileName"] for r in item["options"]]
        label = f"Which {item['resource_type']} should be used for {item['service']}?"
        with st.form(f"resource_{idx}"):
            choice = st.selectbox(label, options)
            submitted = st.form_submit_button("→", type="primary")
        if submitted:
            chosen = next(r for r in item["options"] if r["fileName"] == choice)
            resource_key = f"{item['service']}_{item['resource_type']}"
            agent.automation.resources[resource_key] = Resource(
                service=item["service"],
                type=item["resource_type"],
                resource_type=ResourceType(item["resource_type_id"]),
                id=chosen["fileId"],
                name=chosen["fileName"],
                details=chosen.get("details", {}),
            )
            agent.log_resource_selection(item["service"], item["resource_type"], item["options"], chosen)
            st.session_state.resource_idx += 1
            st.rerun()
    else:
        sync_stage()
        st.rerun()

# ── configuring_parameters ────────────────────────────────────────────────────

elif st.session_state.stage == "configuring_parameters":
    agent = get_agent()

    if not agent.pending_parameter_questions:
        st.session_state.current_question = None
        st.session_state.follow_up_response = None
        with content_guard(), st.spinner("Configuring parameters…"):
            agent.check_status()

    if agent.pending_parameter_questions:
        q = agent.pending_parameter_questions[0]

        # Initialise on first render of this parameter question
        if st.session_state.current_question is None:
            st.session_state.current_question = q["question"]
            st.session_state.follow_up_response = None

        if st.session_state.follow_up_response:
            st.write(st.session_state.follow_up_response)

        form_key = f"param_{q['tool_idx']}_{q['param_name']}_{st.session_state.turn_count}"
        submitted, answer = ask(st.session_state.current_question, form_key)
        if submitted and answer.strip():
            param_def = q.get("param_definition", {})
            options = param_def.get("options") or param_def.get("enum") or []
            context = (
                f"Automation: {agent.automation.description_of_automation}. "
                f"Parameter: {q['param_name']} (type: {param_def.get('type', 'string')}). "
                f"Description: {param_def.get('description', '')}. "
                + (f"Valid options: {options}. " if options else "")
                + (f"Suggested value: {q.get('inferred_value')}." if q.get("inferred_value") else "")
            )
            with content_guard(), st.spinner("Processing…"):
                result = agent._classify_dialogue_turn(
                    st.session_state.current_question, answer, context
                )
            st.session_state.turn_count += 1
            if result.is_answer:
                with content_guard(), st.spinner("Applying…"):
                    agent.apply_parameter_answers([{
                        "tool_idx": q["tool_idx"],
                        "param_name": q["param_name"],
                        "user_input": answer,
                        "question": q.get("question", ""),
                        "inferred_value": q.get("inferred_value"),
                        "param_definition": param_def,
                        "needs_confirmation": q.get("needs_confirmation", False),
                        "available_input_fields": q.get("available_input_fields", {}),
                    }])
                    sync_stage()  # resets current_question + follow_up_response
            else:
                st.session_state.follow_up_response = result.follow_up_response
                st.session_state.current_question = (
                    result.follow_up_question or st.session_state.current_question
                )
            st.rerun()
    else:
        sync_stage()
        st.rerun()

# ── ready_for_aspec ───────────────────────────────────────────────────────────

elif st.session_state.stage == "ready_for_aspec":
    with content_guard(), st.spinner("Generating your automation specification…"):
        get_agent().check_status()
    sync_stage()
    st.rerun()

# ── finished ──────────────────────────────────────────────────────────────────

elif st.session_state.stage == "finished":
    agent = get_agent()
    st.success("Automation specification generated!")

    if agent.aspec:
        if not st.session_state.get("aspec_saved"):
            path = _save_aspec_to_file(agent.aspec, agent.scenario)
            st.session_state.aspec_saved = True
            st.caption(f"💾 Saved to `{path}`")
        st.json(agent.aspec)
    else:
        st.warning("ASPEC not available — check terminal output.")

    if st.button("Start over"):
        st.session_state.agent = None
        st.session_state.stage = "idle"
        st.session_state.scenario = None
        st.session_state.aspec_saved = False
        st.rerun()

# ── unknown / error ───────────────────────────────────────────────────────────

else:
    st.error(f"Unknown stage: {st.session_state.stage}")
    if st.button("Reset"):
        st.session_state.agent = None
        st.session_state.stage = "idle"
        st.rerun()
