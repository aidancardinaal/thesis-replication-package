"""
Tool Selection Module for Ajora Automation Builder

Handles hierarchical tool selection via multi-stage LLM calls:
1. Domain Selection (trigger + action domains)
2. Action Selection (filtered by is_trigger flag)
3. Adapter Selection (per action)
"""

from typing import List, Tuple
import json
import os
import warnings
from dotenv import load_dotenv

warnings.filterwarnings("ignore", message="Pydantic serializer warnings", category=UserWarning, module="pydantic")
from pydantic import BaseModel, Field
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

from llm_safety import safe_invoke, ContentPolicyError

load_dotenv()

OLLAMA_MODEL = "gemma4:e2b"
AZURE_DEPLOYMENT = "gpt-5.4-mini"

def get_llm():
    api_key = os.getenv("AZURE_API_KEY")
    if api_key:
        return AzureChatOpenAI(
            azure_deployment=AZURE_DEPLOYMENT,
            azure_endpoint=os.getenv("AZURE_ENDPOINT"),
            api_key=api_key,
            api_version=os.getenv("AZURE_API_VERSION", "2025-01-01-preview"),
            temperature=0,
        )
    return ChatOllama(model=OLLAMA_MODEL, temperature=0)


# ── Output models ─────────────────────────────────────────────────────────────

class DomainSelection(BaseModel):
    trigger_domain: str = Field(description="Domain ID for the trigger (e.g., 'email')")
    action_domains: List[str] = Field(description="List of domain IDs for actions")
    confidence: float = Field(description="Confidence score 0.00–1.00")
    motivation: str = Field(description="Why these domains were chosen")


class ActionSelection(BaseModel):
    selected_action_id: str = Field(description="Selected action ID")
    confidence: float = Field(description="Confidence score 0.00–1.00")
    motivation: str = Field(description="Why this action was chosen")


class MultiActionSelection(BaseModel):
    selected_action_ids: List[str] = Field(
        description="Ordered list of ALL action IDs from this domain that the user's request requires, in execution order"
    )
    confidences: List[float] = Field(
        description="Confidence score (0.00–1.00) for each selected action, in the same order"
    )
    motivations: List[str] = Field(
        description="Brief motivation for each selected action, in the same order"
    )


class AdapterSelection(BaseModel):
    selected_adapter_id: str = Field(description="Selected adapter ID")
    confidence: float = Field(description="Confidence score 0.00–1.00")
    motivation: str = Field(description="Why this adapter was chosen")


class ToolSelectionResult(BaseModel):
    trigger_adapter_id: str
    action_adapter_ids: List[str]
    domain_confidence: float
    trigger_action_confidence: float
    trigger_adapter_confidence: float
    action_confidences: List[float]
    adapter_confidences: List[float]
    domain_motivation: str
    trigger_action_motivation: str
    trigger_adapter_motivation: str
    action_motivations: List[str]
    adapter_motivations: List[str]


class ToolSelectionError(Exception):
    pass


# ── Selector ──────────────────────────────────────────────────────────────────

class ToolSelector:

    def __init__(self, toolbox_path: str = None, toolbox: dict = None):
        if toolbox is not None:
            self.toolbox = toolbox
        elif toolbox_path:
            with open(toolbox_path, 'r') as f:
                self.toolbox = json.load(f)
        else:
            with open("action_catalogue.json", 'r') as f:
                self.toolbox = json.load(f)

        self.llm = get_llm()
        self.domains_dict = {d['id']: d for d in self.toolbox.get('domains', [])}
        self.actions_dict = {a['id']: a for a in self.toolbox.get('actions', [])}
        self.adapters_dict = {a['id']: a for a in self.toolbox.get('adapters', [])}

    def select_tools_for_automation(self, user_prompt: str) -> ToolSelectionResult:
        print("\n" + "="*60)
        print("TOOL SELECTION PIPELINE")
        print("="*60)

        print("\n[Stage 1/3] Selecting domains...")
        trigger_domain_id, action_domain_ids, domain_conf, domain_motiv = self._select_domains(user_prompt)
        print(f"✓ Trigger domain: {trigger_domain_id} (confidence: {domain_conf:.2f})")
        print(f"✓ Action domains: {', '.join(action_domain_ids)}")

        print("\n[Stage 2/3] Selecting actions...")
        (trigger_action_id, action_ids,
         trigger_action_conf, trigger_action_motiv,
         action_confs, action_motivs) = self._select_actions(user_prompt, trigger_domain_id, action_domain_ids)
        print(f"✓ Trigger action: {trigger_action_id} (confidence: {trigger_action_conf:.2f})")
        for aid, conf in zip(action_ids, action_confs):
            print(f"✓ Action: {aid} (confidence: {conf:.2f})")

        print("\n[Stage 3/3] Selecting adapters...")
        (trigger_adapter_id, action_adapter_ids,
         trigger_adapter_conf, trigger_adapter_motiv,
         adapter_confs, adapter_motivs) = self._select_adapters(user_prompt, trigger_action_id, action_ids)
        print(f"✓ Trigger adapter: {trigger_adapter_id} (confidence: {trigger_adapter_conf:.2f})")
        for aid, conf in zip(action_adapter_ids, adapter_confs):
            print(f"✓ Action adapter: {aid} (confidence: {conf:.2f})")

        print("\n" + "="*60 + "\n")

        return ToolSelectionResult(
            trigger_adapter_id=trigger_adapter_id,
            action_adapter_ids=action_adapter_ids,
            domain_confidence=domain_conf,
            trigger_action_confidence=trigger_action_conf,
            trigger_adapter_confidence=trigger_adapter_conf,
            action_confidences=action_confs,
            adapter_confidences=adapter_confs,
            domain_motivation=domain_motiv,
            trigger_action_motivation=trigger_action_motiv,
            trigger_adapter_motivation=trigger_adapter_motiv,
            action_motivations=action_motivs,
            adapter_motivations=adapter_motivs,
        )

    def _select_domains(self, user_prompt: str) -> Tuple[str, List[str], float, str]:
        available_domains = [
            {"id": d['id'], "name": d['name'], "description": d['description'],
             "keywords": d.get('intent_keywords', []), "examples": d.get('examples', [])}
            for d in self.toolbox.get('domains', [])
            if not d.get('internal_only', False)
        ]
        domains_text = "\n".join([
            f"- {d['id']}: {d['name']} - {d['description']}\n"
            f"  Keywords: {', '.join(d['keywords'])}\n"
            f"  Examples: {', '.join(d['examples'][:2])}"
            for d in available_domains
        ])

        prompt = ChatPromptTemplate.from_messages([
            ("system",
                "You are an assistant that selects the most appropriate domains for an automation. "
                "The trigger_domain is what starts the automation. "
                "The action_domains are what the automation does in response. "
                "Provide a confidence score (0.00–1.00) and a brief motivation."),
            ("user",
                "Based on the user's automation request, select the domains.\n\n"
                "Available domains:\n{domains_text}\n\n"
                "User request: {request}")
        ])

        try:
            result = safe_invoke(prompt | self.llm.with_structured_output(DomainSelection), {
                "domains_text": domains_text,
                "request": user_prompt,
            })
            return result.trigger_domain, result.action_domains, result.confidence, result.motivation
        except ContentPolicyError:
            raise
        except Exception as e:
            raise ToolSelectionError(f"Domain selection failed: {e}")

    def _select_actions(
        self, user_prompt: str, trigger_domain_id: str, action_domain_ids: List[str]
    ) -> Tuple[str, List[str], float, str, List[float], List[str]]:
        if trigger_domain_id not in self.domains_dict:
            raise ToolSelectionError(f"Trigger domain '{trigger_domain_id}' not found")

        trigger_domain = self.domains_dict[trigger_domain_id]
        trigger_actions = [
            self.actions_dict[aid]
            for aid in trigger_domain.get('children', [])
            if aid in self.actions_dict and self.actions_dict[aid].get('is_trigger') == 'true'
        ]
        if not trigger_actions:
            raise ToolSelectionError(f"No trigger actions for domain '{trigger_domain_id}'")

        trigger_action_id, trigger_action_conf, trigger_action_motiv = self._select_single_action(
            user_prompt, trigger_actions, trigger_domain['name'], is_trigger=True
        )

        selected_action_ids, action_confidences, action_motivations = [], [], []
        for action_domain_id in action_domain_ids:
            if action_domain_id not in self.domains_dict:
                raise ToolSelectionError(f"Action domain '{action_domain_id}' not found")
            action_domain = self.domains_dict[action_domain_id]
            actions = [
                self.actions_dict[aid]
                for aid in action_domain.get('children', [])
                if aid in self.actions_dict and self.actions_dict[aid].get('is_trigger') == 'false'
            ]
            if not actions:
                raise ToolSelectionError(f"No actions for domain '{action_domain_id}'")
            ids, confs, motivs = self._select_multiple_actions(
                user_prompt, actions, action_domain['name']
            )
            selected_action_ids.extend(ids)
            action_confidences.extend(confs)
            action_motivations.extend(motivs)

        return (trigger_action_id, selected_action_ids,
                trigger_action_conf, trigger_action_motiv,
                action_confidences, action_motivations)

    def _select_multiple_actions(
        self, user_prompt: str, actions: List[dict], domain_name: str
    ) -> Tuple[List[str], List[float], List[str]]:
        actions_text = "\n".join([
            f"- {a['id']}: {a['name']} - {a['description']}\n"
            f"  Keywords: {', '.join(a.get('intent_keywords', []))}\n"
            f"  Examples: {', '.join(a.get('examples', [])[:2])}"
            for a in actions
        ])

        prompt = ChatPromptTemplate.from_messages([
            ("system",
                f"You are an assistant that selects all appropriate actions from a domain for an automation. "
                f"Select ALL actions in domain '{domain_name}' that the user's request requires, in execution order. "
                f"There may be one or more. Provide a confidence score and motivation per action."),
            ("user",
                f"Based on the user's automation request, select all needed actions.\n\n"
                f"Available actions in domain '{domain_name}':\n{{actions_text}}\n\n"
                f"User request: {{user_prompt}}")
        ])

        try:
            result = safe_invoke(prompt | self.llm.with_structured_output(MultiActionSelection), {
                "actions_text": actions_text,
                "user_prompt": user_prompt,
            })
            if not result.selected_action_ids:
                raise ToolSelectionError(f"No action IDs returned for domain '{domain_name}'")
            for aid in result.selected_action_ids:
                if aid not in self.actions_dict:
                    raise ToolSelectionError(f"Selected action '{aid}' not in catalogue")
            n = len(result.selected_action_ids)
            confs = (result.confidences + [0.0] * n)[:n]
            motivs = (result.motivations + [""] * n)[:n]
            return result.selected_action_ids, confs, motivs
        except ContentPolicyError:
            raise
        except Exception as e:
            raise ToolSelectionError(f"Action selection failed: {e}")

    def _select_single_action(
        self, user_prompt: str, actions: List[dict], domain_name: str, is_trigger: bool
    ) -> Tuple[str, float, str]:
        action_type = "trigger action" if is_trigger else "action"
        actions_text = "\n".join([
            f"- {a['id']}: {a['name']} - {a['description']}\n"
            f"  Keywords: {', '.join(a.get('intent_keywords', []))}\n"
            f"  Examples: {', '.join(a.get('examples', [])[:2])}"
            for a in actions
        ])

        prompt = ChatPromptTemplate.from_messages([
            ("system",
                f"You are an assistant that selects the most appropriate {action_type}. "
                f"Provide a confidence score (0.00–1.00) and a brief motivation."),
            ("user",
                f"Based on the user's automation request, select the {action_type}.\n\n"
                f"Available {action_type}s in domain '{domain_name}':\n{{actions_text}}\n\n"
                f"User request: {{user_prompt}}")
        ])

        try:
            result = safe_invoke(prompt | self.llm.with_structured_output(ActionSelection), {
                "actions_text": actions_text,
                "user_prompt": user_prompt,
            })
            if not result.selected_action_id:
                raise ToolSelectionError(f"No {action_type} ID returned")
            if result.selected_action_id not in self.actions_dict:
                raise ToolSelectionError(f"Selected {action_type} '{result.selected_action_id}' not in catalogue")
            return result.selected_action_id, result.confidence, result.motivation
        except ContentPolicyError:
            raise
        except Exception as e:
            raise ToolSelectionError(f"Action selection failed: {e}")

    def _select_adapters(
        self, user_prompt: str, trigger_action_id: str, action_ids: List[str]
    ) -> Tuple[str, List[str], float, str, List[float], List[str]]:
        trigger_action = self.actions_dict[trigger_action_id]
        trigger_adapters = [
            self.adapters_dict[aid]
            for aid in trigger_action.get('children', [])
            if aid in self.adapters_dict
        ]
        if not trigger_adapters:
            raise ToolSelectionError(f"No adapters for trigger action '{trigger_action_id}'")

        trigger_adapter_id, trigger_adapter_conf, trigger_adapter_motiv = self._select_single_adapter(
            user_prompt, trigger_adapters, trigger_action['name']
        )

        # Describe steps selected so far (with what they produce) so later adapter
        # choices can check whether a stated prior-step requirement (e.g. "needs a
        # message ID from an earlier step") is actually satisfiable within this
        # automation, instead of guessing in a vacuum.
        output_schemas = self.toolbox.get('output_schemas', {})
        trigger_adapter = self.adapters_dict[trigger_adapter_id]
        selected_steps = [self._describe_selected_step(trigger_action['name'], trigger_adapter, output_schemas)]

        selected_adapter_ids, adapter_confidences, adapter_motivations = [], [], []
        for action_id in action_ids:
            action = self.actions_dict[action_id]
            adapters = [
                self.adapters_dict[aid]
                for aid in action.get('children', [])
                if aid in self.adapters_dict
            ]
            if not adapters:
                raise ToolSelectionError(f"No adapters for action '{action_id}'")
            adapter_id, adapter_conf, adapter_motiv = self._select_single_adapter(
                user_prompt, adapters, action['name'], prior_steps="\n".join(selected_steps)
            )
            selected_adapter_ids.append(adapter_id)
            adapter_confidences.append(adapter_conf)
            adapter_motivations.append(adapter_motiv)
            selected_steps.append(
                self._describe_selected_step(action['name'], self.adapters_dict[adapter_id], output_schemas)
            )

        return (trigger_adapter_id, selected_adapter_ids,
                trigger_adapter_conf, trigger_adapter_motiv,
                adapter_confidences, adapter_motivations)

    def _describe_selected_step(self, step_name: str, adapter: dict, output_schemas: dict) -> str:
        """One-line summary of what a selected step produces, grounded in the
        catalogue's own output-schema descriptions (no hand-authored labels)."""
        fields = []
        for out_name in adapter.get('outputs', []):
            schema = output_schemas.get(out_name, {})
            field_descs = [f"{k} ({v.get('description', '')})" for k, v in schema.get('properties', {}).items()]
            if field_descs:
                fields.append(f"{out_name} — {', '.join(field_descs)}")
        produces = "; ".join(fields) if fields else "nothing usable by later steps"
        return f"- {step_name} ({adapter['name']}): produces {produces}"

    def _select_single_adapter(
        self, user_prompt: str, adapters: List[dict], action_name: str, prior_steps: str = ""
    ) -> Tuple[str, float, str]:
        adapters_text = "\n".join([
            f"- {a['id']}: {a['name']} (Service: {a.get('service', 'unknown')}) - {a['description']}\n"
            f"  Keywords: {', '.join(a.get('intent_keywords', []))}"
            for a in adapters
        ])

        prior_steps_note = (
            f"\n\nSteps already selected for this automation, in order, and what each produces:\n{prior_steps}\n\n"
            "If an adapter's description says it needs data from a prior step (e.g. an ID or reference), "
            "check whether any step listed above could plausibly supply it. If so, that requirement is "
            "satisfied — the pipeline wires such references between steps automatically — and should NOT "
            "lower your confidence. Reserve low confidence for genuine mismatches in service or capability, "
            "not for data-availability questions the steps above already answer."
            if prior_steps else ""
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system",
                "You are an assistant that selects the appropriate adapter/service. "
                "Provide a confidence score (0.00–1.00) and a brief motivation."
                f"{prior_steps_note}"),
            ("user",
                f"Based on the user's automation request, select the adapter.\n\n"
                f"Available adapters for '{action_name}':\n{{adapters_text}}\n\n"
                f"User request: {{user_prompt}}")
        ])

        try:
            result = safe_invoke(prompt | self.llm.with_structured_output(AdapterSelection), {
                "adapters_text": adapters_text,
                "user_prompt": user_prompt,
            })
            if not result.selected_adapter_id:
                raise ToolSelectionError("No adapter ID returned")
            if result.selected_adapter_id not in self.adapters_dict:
                raise ToolSelectionError(f"Selected adapter '{result.selected_adapter_id}' not in catalogue")
            return result.selected_adapter_id, result.confidence, result.motivation
        except ContentPolicyError:
            raise
        except Exception as e:
            raise ToolSelectionError(f"Adapter selection failed: {e}")


# ── Public API ────────────────────────────────────────────────────────────────

def select_tools_for_automation(user_prompt: str, toolbox: dict = None) -> ToolSelectionResult:
    return ToolSelector(toolbox=toolbox).select_tools_for_automation(user_prompt)


if __name__ == "__main__":
    result = select_tools_for_automation(
        "Read every email I receive in my inbox and add a snippet to a google sheet."
    )
    print(f"Trigger: {result.trigger_adapter_id}")
    print(f"Actions: {result.action_adapter_ids}")
