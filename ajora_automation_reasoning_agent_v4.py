from typing import Dict, List, Optional, Any
import ast
import json
import os
import re
import warnings
from datetime import datetime

warnings.filterwarnings("ignore", message="Pydantic serializer warnings", category=UserWarning, module="pydantic")

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_openai import AzureChatOpenAI
from langchain_core.prompts import ChatPromptTemplate

load_dotenv()

from classes import Credential, ResourceType, Resource, ToolStep, Tools, Automation
from aspec_mapper import automation_to_aspec, validate_automation_for_aspec
from tool_selector import select_tools_for_automation, ToolSelectionError
from llm_safety import safe_invoke, ContentPolicyError

OLLAMA_MODEL = "gemma4:e2b"
AZURE_DEPLOYMENT = "gpt-5.4-mini"

def get_llm():
    api_key = os.getenv("AZURE_API_KEY")
    if api_key:
        print(f"Using Azure OpenAI ({AZURE_DEPLOYMENT})")
        return AzureChatOpenAI(
            azure_deployment=AZURE_DEPLOYMENT,
            azure_endpoint=os.getenv("AZURE_ENDPOINT"),
            api_key=api_key,
            api_version=os.getenv("AZURE_API_VERSION", "2025-01-01-preview"),
            temperature=0,
        )
    print(f"Using Ollama ({OLLAMA_MODEL})")
    return ChatOllama(model=OLLAMA_MODEL, temperature=0)

llm = get_llm()


# ── LLM output models ─────────────────────────────────────────────────────────

class ClarificationQuestion(BaseModel):
    question: str = Field(description="The question to ask, or 'false' if no clarification is needed.")
    motivation: str = Field(description="Reasoning behind the question.")

class RefinedAutomationDescription(BaseModel):
    refined_description: str = Field(description="Refined automation description incorporating the clarification.")
    motivation: str = Field(description="Reasoning behind the refinement.")

class LLMOutput(BaseModel):
    output: str = Field(description="The inferred parameter value as a string.")
    motivation: str = Field(description="Reasoning behind the output.")
    probability: float = Field(description="Confidence level 0.0–1.0.")

class DialogueTurnResult(BaseModel):
    is_answer: bool = Field(
        description="True if the user's input is a direct, complete, and usable answer to the question. "
                    "False if it is a follow-up question, a request for clarification, or an answer that is "
                    "too vague or incomplete to use (e.g. 'send it to my colleague' when a specific email address is needed)."
    )
    follow_up_response: str = Field(
        description="If is_answer is False: a plain-language response addressing what the user asked or "
                    "explaining what specific information is still needed. Empty string if is_answer is True."
    )
    follow_up_question: str = Field(
        description="If is_answer is False: a short, natural follow-on question that continues the conversation "
                    "after the follow_up_response — e.g. 'Which of those would you prefer?' or "
                    "'What is your colleague's email address?'. Empty string if is_answer is True."
    )

class AutomationName(BaseModel):
    name: str = Field(description="A concise, human-readable name for the automation in one sentence or less.")


# ── Agent ─────────────────────────────────────────────────────────────────────

class AjoraAgent:
    """
    Status-driven agent for building automations.

    Status flow:
    clarifying → configuring_tools → configuring_credentials →
    configuring_resources → configuring_parameters → ready_for_aspec → finished
    """

    def __init__(self, toolbox: Optional[Dict[str, Any]] = None,
                 uservault: Optional[Dict[str, Any]] = None,
                 api_client=None):
        if toolbox is not None:
            self.toolbox = toolbox
        else:
            try:
                with open('thesis-new-pipeline/action_catalogue.json', 'r') as f:
                    self.toolbox = json.load(f)
            except FileNotFoundError:
                self.toolbox = {"adapters": []}
                print("Warning: action_catalogue.json not found, using empty toolbox.")

        if uservault is not None:
            self.uservault = uservault
        else:
            try:
                with open('thesis-new-pipeline/uservault.json', 'r') as f:
                    self.uservault = json.load(f)
            except FileNotFoundError:
                self.uservault = {"credentials": [], "resources": []}
                print("Warning: uservault.json not found, using empty uservault.")

        self.automation = Automation(
            description_of_automation="",
            status="clarifying",
            tools=Tools(),
        )

        self.api_client = api_client
        self.aspec: Optional[dict] = None
        self.scenario: Optional[str] = None

        self._clarification_history: List[Dict[str, str]] = []  # [{question, answer}, ...]
        self._clarification_rounds: int = 0
        self._min_clarification_rounds: int = 3
        self._max_clarification_rounds: int = 5

        # Pending state (set by check_status, consumed by caller / Streamlit)
        self.pending_clarification_question: Optional[str] = None
        self.pending_missing_credentials: Optional[List[Dict[str, str]]] = None
        self.pending_missing_resources: Optional[List[Dict[str, Any]]] = None
        self.pending_resource_selection: Optional[List[Dict[str, Any]]] = None
        self.pending_parameter_questions: Optional[List[Dict]] = None

        # Interaction log (populated throughout; saved when ASPEC is generated)
        self.interaction_log: List[Dict[str, str]] = []
        self._initial_description: Optional[str] = None

    # ── Clarification ─────────────────────────────────────────────────────────

    def generate_clarification_question_or_complete(self, description_of_automation: str, force_question: bool = False) -> dict:
        if force_question:
            completion_instruction = (
                "You must ask one more clarifying question — do NOT set question to 'false'. "
                "Focus on any functional detail that could still be made more specific: "
                "exact data fields, frequency, conditions, or destination format."
            )
        else:
            completion_instruction = (
                "If all required information is present, set question to 'false'. "
                "Otherwise, set question to a single clarifying question."
            )
        prompt = ChatPromptTemplate.from_messages([
            ("system",
                "You are an assistant that helps users define automations in simple, clear language. "
                "The users are non-technical, so questions must be short, concrete, and easy to understand. "
                "Avoid technical jargon, references to APIs, nodes, or credentials. "
                "Never ask about authentication or security — those are handled automatically. "
                "Ask only a single question at a time. "
                "You need to know what services the user wants to connect (e.g. if they say 'email', ask which service: Gmail, Outlook, etc.). "
                "Focus only on missing functional details (trigger, action, data destination). "
                f"{completion_instruction}"),
            ("user",
                "Analyze this automation description and determine if you need one more clarification.\n\n"
                "Description: {description_of_automation}")
        ])

        result = safe_invoke(prompt | llm.with_structured_output(ClarificationQuestion), {
            "description_of_automation": description_of_automation,
        })
        return result.model_dump()

    def refine_automation_description_with_clarification(self, asked_question: str, user_clarification: str):
        prompt = ChatPromptTemplate.from_messages([
            ("system",
                "You are an assistant that helps users define automations in simple, clear language. "
                "Incorporate the user's clarification into the original description, making it more specific. "
                "Avoid technical jargon. Focus on functional details. "
                "Do not change anything except to incorporate the clarification. "
                "Do not make assumptions. Do not include questions in the output."),
            ("user",
                "Original description: {original_description}\n\n"
                "Question asked: {asked_question}\n\n"
                "User's clarification: {user_clarification}\n\n"
                "Incorporate the clarification into the original description.")
        ])

        result = safe_invoke(prompt | llm.with_structured_output(RefinedAutomationDescription), {
            "original_description": self.automation.description_of_automation,
            "asked_question": asked_question,
            "user_clarification": user_clarification,
        })
        result_dict = result.model_dump()
        print(f"Refined description: {result_dict['refined_description']}")
        self.automation.description_of_automation = result_dict["refined_description"]
        self._clarification_history.append({"question": asked_question, "answer": user_clarification})
        self._clarification_rounds += 1
        self.interaction_log.append({
            "stage": self.automation.status,
            "question": asked_question,
            "answer": user_clarification,
            "updated_description": result_dict["refined_description"],
        })

    # ── Tool selection ────────────────────────────────────────────────────────

    def select_tools_for_automation(self):
        try:
            result = select_tools_for_automation(
                self.automation.description_of_automation, toolbox=self.toolbox
            )

            low_confidence_items = []
            if result.domain_confidence < 0.7:
                low_confidence_items.append(("domain selection", result.domain_confidence, result.domain_motivation))
            if result.trigger_action_confidence < 0.7:
                low_confidence_items.append(("trigger action", result.trigger_action_confidence, result.trigger_action_motivation))
            if result.trigger_adapter_confidence < 0.7:
                low_confidence_items.append(("trigger adapter", result.trigger_adapter_confidence, result.trigger_adapter_motivation))
            for i, conf in enumerate(result.action_confidences):
                if conf < 0.7:
                    low_confidence_items.append((f"action {i+1}", conf,
                        result.action_motivations[i] if i < len(result.action_motivations) else ""))
            for i, conf in enumerate(result.adapter_confidences):
                if conf < 0.7:
                    low_confidence_items.append((f"adapter {i+1}", conf,
                        result.adapter_motivations[i] if i < len(result.adapter_motivations) else ""))

            if low_confidence_items:
                item_name, conf, motivation = low_confidence_items[0]
                clarification_prompt = (
                    f"I need more information to choose the right tool for {item_name} "
                    f"(confidence: {conf:.0%}). {motivation} Can you provide more details?"
                )
                print(f"Low confidence in {item_name}, returning to clarification.")
                self.automation.status = "clarifying"
                self.pending_clarification_question = clarification_prompt
                return

            def create_tool_step(adapter_id: str, is_trigger: bool) -> ToolStep:
                adapter_info = next((a for a in self.toolbox['adapters'] if a['id'] == adapter_id), None)
                if not adapter_info:
                    raise ValueError(f"Adapter {adapter_id} not found in catalogue")
                parts = adapter_id.split('.')
                domain = parts[0] if parts else ""
                action_id = '.'.join(parts[:2]) if len(parts) > 1 else ""
                return ToolStep(
                    domain=domain,
                    action_id=action_id,
                    adapter_id=adapter_id,
                    adapter_name=adapter_info.get('name'),
                    service=adapter_info.get('service'),
                    is_trigger=is_trigger,
                    configured_parameters={},
                    missing_parameters=list(adapter_info.get('parameters', {}).keys()),
                    notes=adapter_info.get('description', '')[:100],
                )

            self.automation.tools.trigger = create_tool_step(result.trigger_adapter_id, is_trigger=True)
            self.automation.tools.actions = [
                create_tool_step(aid, is_trigger=False) for aid in result.action_adapter_ids
            ]

            print(f"Trigger: {result.trigger_adapter_id}")
            for action in self.automation.tools.actions:
                print(f"Action: {action.adapter_id}")

            self.automation.status = "configuring_credentials"

        except ToolSelectionError as e:
            print(f"Tool selection failed: {e}")
            self.automation.status = "clarifying"
            self.automation.description_of_automation += " [Tool selection failed — please provide more details]"

    # ── Credentials ───────────────────────────────────────────────────────────

    def _map_auth_type_to_integration_type(self, auth_type: str) -> Optional[str]:
        return {
            "gmailOAuth2": "GoogleGmail",
            "googleSheetsOAuth2Api": "GoogleSheets",
            "googleDriveOAuth2Api": "GoogleDrive",
            "microsoftOutlookOAuth2Api": "MicrosoftOutlook",
            "outlookOAuth2": "MicrosoftOutlook",
        }.get(auth_type)

    def configure_credentials(self):
        print("\n🔐 Checking credentials...")
        required_auth_types = {}
        for tool in [self.automation.tools.trigger] + self.automation.tools.actions:
            adapter_info = next((a for a in self.toolbox['adapters'] if a['id'] == tool.adapter_id), None)
            if adapter_info:
                auth_type = adapter_info.get('auth_type')
                service = adapter_info.get('service')
                if auth_type:
                    if isinstance(auth_type, list):
                        for at in auth_type:
                            required_auth_types[at] = service
                    else:
                        required_auth_types[auth_type] = service

        available_credentials = {}
        for cred in self.uservault.get('credentials', []):
            cred_auth_type = cred.get('auth_type')
            if isinstance(cred_auth_type, list):
                for at in cred_auth_type:
                    available_credentials[at] = cred
            else:
                available_credentials[cred_auth_type] = cred

        missing_auth_types = []
        for auth_type, service in required_auth_types.items():
            if auth_type in available_credentials:
                vault_cred = available_credentials[auth_type]
                self.automation.credentials[auth_type] = Credential(
                    status=vault_cred.get('status', 'ok'),
                    service=service,
                    auth_type=auth_type,
                    credential_id=vault_cred.get('id'),
                    last_checked_at=datetime.now(),
                    notes=f"Found in vault: {vault_cred.get('name')}",
                )
                print(f"✅ {auth_type} found")
            else:
                missing_auth_types.append((auth_type, service))
                self.automation.credentials[auth_type] = Credential(
                    status="missing", service=service, auth_type=auth_type,
                    last_checked_at=datetime.now(), notes="Not found in vault",
                )
                print(f"❌ {auth_type} missing")

        if missing_auth_types:
            self.pending_missing_credentials = [
                {"auth_type": at, "service": svc,
                 "integration_type": self._map_auth_type_to_integration_type(at)}
                for at, svc in missing_auth_types
            ]
            return

        self.automation.status = "configuring_resources"

    # ── Resources ─────────────────────────────────────────────────────────────

    def configure_resources(self):
        print("\n📦 Checking resources...")
        required_resources = {}
        for tool in [self.automation.tools.trigger] + self.automation.tools.actions:
            adapter_info = next((a for a in self.toolbox['adapters'] if a['id'] == tool.adapter_id), None)
            if adapter_info and 'required_resource' in adapter_info:
                resource_req = adapter_info['required_resource']
                required_resources[tool.adapter_id] = {
                    'type': resource_req.get('type'),
                    'resource_type': resource_req.get('resource_type'),
                    'service': adapter_info.get('service'),
                }

        if not required_resources:
            print("✅ No resources required")
            self.automation.status = "configuring_parameters"
            return

        available_resources = self.uservault.get('resources', [])
        missing_resources = []

        for adapter_id, resource_req in required_resources.items():
            resource_type_id = resource_req['resource_type']
            resource_type = resource_req['type']
            service = resource_req['service']
            matching = [r for r in available_resources if r.get('resource_type') == resource_type_id]

            if matching:
                if len(matching) == 1:
                    selected = matching[0]
                    resource_key = f"{service}_{resource_type}"
                    self.automation.resources[resource_key] = Resource(
                        service=service, type=resource_type,
                        resource_type=ResourceType(resource_type_id),
                        id=selected.get('fileId'), name=selected.get('fileName'),
                        details=selected.get('details', {}),
                    )
                    print(f"✅ Auto-selected: {selected.get('fileName')}")
                else:
                    if self.pending_resource_selection is None:
                        self.pending_resource_selection = []
                    self.pending_resource_selection.append({
                        "adapter_id": adapter_id,
                        "resource_type": resource_type,
                        "service": service,
                        "resource_type_id": resource_type_id,
                        "options": matching,
                    })
                    print(f"📋 Multiple options for {resource_type} ({len(matching)} found)")
            else:
                missing_resources.append((adapter_id, resource_type, service, resource_type_id))
                print(f"❌ No {resource_type} found for {adapter_id}")

        if missing_resources:
            self.pending_missing_resources = [
                {"adapter_id": aid, "resource_type": rt, "service": svc, "resource_type_id": rtid}
                for aid, rt, svc, rtid in missing_resources
            ]

        if self.pending_resource_selection or missing_resources:
            return

        self.automation.status = "configuring_parameters"

    # ── Parameter configuration ───────────────────────────────────────────────

    def prefill_resource_parameters(self):
        all_tools = [self.automation.tools.trigger] + self.automation.tools.actions
        for tool in all_tools:
            adapter_info = next((a for a in self.toolbox['adapters'] if a['id'] == tool.adapter_id), None)
            if not adapter_info:
                continue
            required_resource = adapter_info.get('required_resource', {})
            prefill = required_resource.get('prefill', {})
            if not prefill:
                continue
            resource_type_id = required_resource.get('resource_type')
            matched = next(
                (r for r in self.automation.resources.values() if r.resource_type == resource_type_id),
                None
            )
            if not matched:
                continue
            for param_name, resource_field in prefill.items():
                if param_name in tool.missing_parameters:
                    value = getattr(matched, resource_field, None)
                    if value is not None:
                        tool.configured_parameters[param_name] = value
                        tool.parameter_origin[param_name] = "system"
                        tool.missing_parameters.remove(param_name)
                        print(f"  ✅ {param_name} prefilled from resource: {value}")

    def configure_parameters(self):
        self.prefill_resource_parameters()
        print("\n⚙️  Configuring parameters...")
        all_tools = [self.automation.tools.trigger] + self.automation.tools.actions
        questions = []

        output_schemas = self.toolbox.get('output_schemas', {})

        for tool_idx, tool in enumerate(all_tools):
            tool_name = "Trigger" if tool_idx == 0 else f"Action {tool_idx}"
            if not tool.missing_parameters:
                continue

            adapter_info = next((a for a in self.toolbox['adapters'] if a['id'] == tool.adapter_id), None)
            if not adapter_info:
                continue

            # Collect all fields available as inputs from preceding steps
            available_input_fields: Dict[str, Any] = {}
            for prev_tool in all_tools[:tool_idx]:
                prev_adapter = next((a for a in self.toolbox['adapters'] if a['id'] == prev_tool.adapter_id), None)
                if prev_adapter:
                    for output_name in prev_adapter.get('outputs', []):
                        schema = output_schemas.get(output_name, {})
                        available_input_fields[output_name] = schema

            for param_name in tool.missing_parameters[:]:
                param_def = adapter_info.get('parameters', {}).get(param_name, {})
                default_config = adapter_info.get('default_config', {})
                default_value = default_config.get(param_name)
                has_default = param_name in default_config

                inferred = self._infer_parameter_value(
                    param_name, param_def, default_value, tool, adapter_info, available_input_fields
                )

                if has_default and inferred['confidence'] >= 0.85:
                    # Parameter has a catalogue default and LLM is confident — auto-fill silently.
                    tool.configured_parameters[param_name] = inferred['value']
                    tool.parameter_origin[param_name] = "inferred"
                    tool.missing_parameters.remove(param_name)
                    print(f"  ✅ {param_name} = {inferred['value']} (confidence {inferred['confidence']:.2f})")
                else:
                    # No catalogue default → always confirm inferred value with user.
                    # Low confidence on any parameter → also ask.
                    question_text = self._generate_user_friendly_prompt(
                        param_name, param_def, tool, inferred, needs_confirmation=not has_default,
                        available_input_fields=available_input_fields,
                    )
                    if not has_default:
                        print(f"  💬 {param_name} has no default — confirming with user (inferred: {inferred['value']})")
                    else:
                        print(f"  ⚠️  {param_name} needs clarification (confidence {inferred['confidence']:.2f})")
                    questions.append({
                        "tool_idx": tool_idx,
                        "adapter_id": tool.adapter_id,
                        "tool_name": tool_name,
                        "param_name": param_name,
                        "question": question_text,
                        "inferred_value": inferred['value'],
                        "param_definition": param_def,
                        "needs_confirmation": not has_default,
                        "available_input_fields": available_input_fields or {},
                    })

        if questions:
            self.pending_parameter_questions = questions
            return

        if all(len(t.missing_parameters) == 0 for t in all_tools):
            self.automation.status = "ready_for_aspec"
            print("✅ All parameters configured")

    def apply_parameter_answers(self, answers: List[Dict]) -> None:
        all_tools = [self.automation.tools.trigger] + self.automation.tools.actions
        for answer in answers:
            tool = all_tools[answer["tool_idx"]]
            adapter_info = next((a for a in self.toolbox['adapters'] if a['id'] == tool.adapter_id), None)
            param_def = answer.get("param_definition") or (
                adapter_info.get('parameters', {}).get(answer["param_name"], {}) if adapter_info else {}
            )
            interpreted = self._interpret_user_answer(
                user_input=answer["user_input"],
                question_asked=answer.get("question", ""),
                param_name=answer["param_name"],
                param_definition=param_def,
                inferred_value=answer.get("inferred_value"),
                needs_confirmation=answer.get("needs_confirmation", False),
                available_input_fields=answer.get("available_input_fields"),
            )
            output_schemas = self.toolbox.get('output_schemas', {})
            for w in self._validate_expression_references(interpreted, param_def, output_schemas):
                print(f"  ⚠️  Expression warning ({answer['param_name']}): {w}")
            tool.configured_parameters[answer["param_name"]] = interpreted
            tool.parameter_origin[answer["param_name"]] = "user"
            if answer["param_name"] in tool.missing_parameters:
                tool.missing_parameters.remove(answer["param_name"])
            self.refine_automation_description_with_clarification(
                asked_question=answer.get("question", ""),
                user_clarification=answer["user_input"],
            )
            self.interaction_log[-1]["parameter_set"] = {
                "param_name": answer["param_name"],
                "value": interpreted,
            }

        self.pending_parameter_questions = None
        all_tools = [self.automation.tools.trigger] + self.automation.tools.actions
        if all(len(t.missing_parameters) == 0 for t in all_tools):
            self.automation.status = "ready_for_aspec"

    def generate_metadata_name(self) -> None:
        chain = (
            ChatPromptTemplate.from_messages([
                ("system",
                 "You generate concise, human-readable names for workflow automations. "
                 "Return a single short name — one sentence maximum, no punctuation at the end."),
                ("human",
                 "Automation description: {description}\n\n"
                 "Generate a name for this automation."),
            ])
            | llm.with_structured_output(AutomationName)
        )
        result = safe_invoke(chain, {"description": self.automation.description_of_automation})
        self.automation.metadata_name = result.name
        print(f"📛 Automation name: {result.name}")

    def log_resource_selection(self, service: str, resource_type: str,
                               options: List[Dict], chosen: Dict) -> None:
        option_names = [o["fileName"] for o in options]
        self.interaction_log.append({
            "stage": "configuring_resources",
            "question": f"Which {resource_type} for {service}? (options: {option_names})",
            "answer": chosen["fileName"],
        })

    def save_checkpoint(self) -> str:
        """
        Persist full agent state so progress is never lost on a mid-run crash.

        Written once per turn. If a content-filter false positive aborts a step,
        the prior good state is already on disk and the run can resume in-session
        (the participant just re-answers the rejected step) rather than restarting.
        """
        script_dir = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.join(script_dir, "scenarios/checkpoints")
        os.makedirs(out_dir, exist_ok=True)
        prefix = f"scenario-{self.scenario}" if self.scenario else "scenario-unknown"
        path = os.path.join(out_dir, f"{prefix}_latest.json")
        state = {
            "scenario": self.scenario,
            "saved_at": datetime.now().isoformat(),
            "status": self.automation.status,
            "automation": self.automation.model_dump(mode="json"),
            "clarification_history": self._clarification_history,
            "clarification_rounds": self._clarification_rounds,
            "interaction_log": self.interaction_log,
            "initial_description": self._initial_description,
        }
        with open(path, "w") as f:
            json.dump(state, f, indent=2)
        return path

    def save_interaction_log(self) -> str:
        script_dir = os.path.dirname(os.path.abspath(__file__))
        out_dir = os.path.join(script_dir, "scenarios/interaction-logs")
        os.makedirs(out_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
        prefix = f"scenario-{self.scenario}" if self.scenario else "scenario-unknown"
        filename = f"{prefix}_{timestamp}.json"
        path = os.path.join(out_dir, filename)
        log_data = {
            "scenario": self.scenario,
            "timestamp": timestamp,
            "initial_description": self._initial_description,
            "interactions": self.interaction_log,
        }
        with open(path, "w") as f:
            json.dump(log_data, f, indent=2)
        return path

    # ── LLM helpers ───────────────────────────────────────────────────────────

    def _infer_parameter_value(self, param_name: str, param_definition: dict,
                                default_value: Any, tool: ToolStep, adapter_info: dict,
                                available_input_fields: Dict[str, Any] = None) -> dict:
        # Build expression examples from the actual available outputs so the LLM
        # anchors to the right output names rather than always defaulting to email_object.
        _example_parts = []
        for _out_name, _schema in (available_input_fields or {}).items():
            if _schema:
                _first_field = next(iter(_schema.get('properties', {})), 'id')
                _example_parts.append("{{{{" + _out_name + "." + _first_field + "}}}}")
        _example_str = ", ".join(_example_parts[:3]) if _example_parts else "{{{{email_object.subject}}}}"

        prompt = ChatPromptTemplate.from_messages([
            ("system",
                "You are an expert at configuring automation parameters. "
                "Given context about an automation, infer the best value for a parameter. "
                "Use the automation description, configured resources, available input fields from previous steps, "
                "and other context to make an informed decision. "
                "Return your inferred value, reasoning, and confidence (0.0–1.0).\n\n"
                "IMPORTANT expression syntax rule:\n"
                "When a parameter value must reference data from a previous step's output, always use "
                "the format {{{{output_name.field_name}}}} — for example " + _example_str + ". "
                "Only reference output names listed in the available input fields. "
                "Never use {{{{$json.*}}}} or any other syntax.\n\n"
                "IMPORTANT confidence calibration rules:\n"
                "- If the user explicitly stated the value in their description, confidence may be 0.9+.\n"
                "- If the value can be derived without ambiguity from a selected resource (e.g. documentId from the chosen spreadsheet), confidence may be 0.9+.\n"
                "- If you are applying a catalogue default unchanged, set confidence to 0.75 at most.\n"
                "- If the value requires choosing among multiple options the user has not addressed (e.g. which columns to map, which fields to include), set confidence to 0.5 or lower.\n"
                "- Never let the existence of a 'reasonable' value inflate confidence above 0.85 when the user has not specified or confirmed it."),
            ("user",
                "Automation description: {automation_description}\n\n"
                "Tool: {adapter_name} - {adapter_description}\n\n"
                "Parameter to configure: {parameter_name}\n"
                "Parameter definition: {parameter_definition}\n"
                "Default value available: {default_value}\n\n"
                "Available input fields from previous steps: {available_input_fields}\n\n"
                "Configured resources: {configured_resources}\n\n"
                "Configured credentials: {configured_credentials}\n\n"
                "Already configured parameters: {already_configured_parameters}")
        ])

        result = safe_invoke(prompt | llm.with_structured_output(LLMOutput), {
            'automation_description': self.automation.description_of_automation,
            'adapter_name': adapter_info.get('name'),
            'adapter_description': adapter_info.get('description'),
            'parameter_name': param_name,
            'parameter_definition': json.dumps(param_definition, indent=2),
            'default_value': default_value if default_value is not None else "None",
            'available_input_fields': json.dumps(available_input_fields or {}, indent=2),
            'configured_resources': json.dumps(
                {k: v.model_dump() for k, v in self.automation.resources.items()}, indent=2),
            'configured_credentials': json.dumps(
                {k: v.service for k, v in self.automation.credentials.items()}, indent=2),
            'already_configured_parameters': json.dumps(tool.configured_parameters, indent=2),
        })

        value = self._parse_typed_value(result.output, param_definition)
        return {'value': value, 'confidence': result.probability, 'reasoning': result.motivation}

    def _parse_typed_value(self, raw: str, param_definition: dict) -> Any:
        param_type = param_definition.get('type', 'string')
        if param_type in ('mapping', 'object'):
            return _parse_object_str(raw)
        if param_type == 'integer':
            try:
                return int(raw)
            except ValueError:
                return raw
        if param_type == 'boolean':
            return raw.lower() in ('yes', 'true', '1', 'y')
        if param_type == 'array':
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return [item.strip() for item in raw.split(',')]
        return raw

    def _generate_user_friendly_prompt(self, param_name: str, param_definition: dict,
                                        tool: ToolStep, inferred_value: dict,
                                        needs_confirmation: bool = False,
                                        available_input_fields: Optional[Dict[str, Any]] = None) -> str:
        raw_value = str(inferred_value.get('value', ''))
        # Internal expression references like {{email_object.body}} must never be shown
        # as raw syntax. Four cases:
        #   - pure reference to a prior step's output (e.g. {{send_result.messageId}})
        #     → resolve it against the catalogue's own output-schema descriptions and
        #     let the LLM phrase a natural confirmation grounded in that meaning
        #     (is_reference = True);
        #   - pure mapping/object value → nothing meaningful to preview, ask plainly;
        #   - mixed natural text + insertions (a message body) → show with readable
        #     [bracket] placeholders so the user can confirm the wording;
        #   - plain value → show as-is.
        param_type = param_definition.get('type', 'string')
        has_expression = bool(_EXPRESSION_TOKEN.search(raw_value))
        is_reference = False
        if not raw_value:
            display_value = None
        elif not has_expression:
            display_value = raw_value
        elif param_type in ('mapping', 'object'):
            display_value = None
        else:
            residual = _strip_expression_tokens(raw_value).strip(" \t\n,.;:-\"'")
            if residual:
                display_value = humanize_expressions(raw_value)
            else:
                display_value = _describe_expression(raw_value, available_input_fields or {})
                is_reference = bool(display_value)

        expression_rule = (
            "IMPORTANT: Never write raw technical syntax such as {{object.field}} in your question. "
            "If the suggested value contains readable placeholders in square brackets (e.g. [Expense name], "
            "[email body]), keep them exactly as written — they stand for live values inserted when the "
            "automation runs. If there is no usable suggested value, simply ask for the detail in plain language. "
        )

        if needs_confirmation and display_value and is_reference:
            instruction = (
                "You are helping a non-technical user configure an automation. "
                "This setting will be filled in automatically at runtime from data produced by an "
                "earlier step — the text below describes what that data represents (in the catalogue's "
                "own technical terms). Translate it into a short, friendly confirmation that describes "
                "the data in plain, concrete terms (e.g. 'the email that was just sent') and asks the "
                "user to confirm the system should use it, or say if they'd like something different. "
                "Do NOT quote the technical description verbatim, and do NOT use internal parameter "
                "names or jargon. Keep it short and conversational."
            )
            task = (
                "Automation: {automation_description}\n"
                "Tool being configured: {adapter_name} — {adapter_description}\n"
                "Setting (technical): {param_name} — {param_description}\n"
                "What this will automatically be filled with (technical description): {suggested_value}\n\n"
                "Write a short, friendly confirmation question about using that automatically-filled value."
            )
        elif needs_confirmation and display_value:
            instruction = (
                "You are helping a non-technical user configure an automation. "
                "You have inferred a value for a setting. Show it and ask the user to confirm or correct it. "
                "Do NOT use internal parameter names or technical jargon. "
                f"{expression_rule}"
                "Keep it short and conversational."
            )
            task = (
                "Automation: {automation_description}\n"
                "Tool being configured: {adapter_name} — {adapter_description}\n"
                "Setting (technical): {param_name} — {param_description}\n"
                "Inferred value: {suggested_value}\n\n"
                "Write a short, friendly confirmation question. Reproduce the inferred value exactly — "
                "including any [bracketed placeholders] — and ask the user to confirm or correct it."
            )
        else:
            instruction = (
                "You are helping a non-technical user configure an automation. "
                "Generate a single, simple question to ask them for a missing detail. "
                "Do NOT use the internal parameter name or any technical jargon. "
                "Frame the question entirely in terms of what the user wants to achieve. "
                f"{expression_rule}"
                "If there is a plain-language suggested value, mention it as the default option. "
            )
            task = (
                "Automation: {automation_description}\n"
                "Tool being configured: {adapter_name} — {adapter_description}\n"
                "Missing detail (technical): {param_name} — {param_description}\n"
                "Suggested value: {suggested_value}\n\n"
                "Write a friendly, plain-language question for a non-technical user."
            )

        prompt = ChatPromptTemplate.from_messages([
            ("system", instruction),
            ("user", task),
        ])

        result = safe_invoke(prompt | llm.with_structured_output(ClarificationQuestion), {
            'automation_description': self.automation.description_of_automation,
            'adapter_name': tool.adapter_name,
            'adapter_description': param_definition.get('description', ''),
            'param_name': param_name,
            'param_description': param_definition.get('description', ''),
            'suggested_value': display_value if display_value else 'none',
        })
        # Final guard: never let raw {{...}} syntax reach the user, even if the LLM echoes it.
        return _explain_placeholders(humanize_expressions(result.question))

    def _classify_dialogue_turn(self, question: str, user_input: str, context: str) -> DialogueTurnResult:
        prompt = ChatPromptTemplate.from_messages([
            ("system",
                "You are helping a non-technical user answer a question about their automation setup. "
                "Decide whether their response is a complete, usable answer or not. "
                "Set is_answer to False in any of these cases:\n"
                "  1. The user asked a follow-up question or requested clarification.\n"
                "  2. The answer is too vague or incomplete to use directly "
                "     (e.g. 'send it to my colleague' when a specific email address is needed, "
                "     or 'some folder' when a specific folder name is needed).\n"
                "     Do note that placeholders are allowed if the user is referring to data from a previous step (e.g. 'the subject line from the email'), as long as it's clear what they mean.\n"
                "  3. The user expressed uncertainty without committing to a value.\n"
                "If is_answer is False, write follow_up_response as a direct reply TO the user (second person, 'you'/'your'). "
                "Address their question or explain what specific information is still needed. "
                "Do not describe the user in third person — you are speaking directly to them. "
                "Write follow_up_question as a short, natural question to ask after the response — "
                "do NOT repeat the original question verbatim; adapt it to continue the conversation naturally. "
                "If the context lists valid options, weave them into follow_up_question. "
                "Never use technical jargon."),
            ("user",
                "Question asked to user: {question}\n"
                "User's response: {user_input}\n"
                "Context: {context}\n\n"
                "Is this a complete, usable answer?"),
        ])
        result = safe_invoke(prompt | llm.with_structured_output(DialogueTurnResult), {
            "question": question,
            "user_input": user_input,
            "context": context,
        })
        # Guard: the context can contain raw {{...}} expressions; never echo them to the user.
        result.follow_up_response = humanize_expressions(result.follow_up_response)
        result.follow_up_question = humanize_expressions(result.follow_up_question)
        return result

    def _interpret_user_answer(self, user_input: str, question_asked: str,
                                param_name: str, param_definition: dict,
                                inferred_value: Any, needs_confirmation: bool = False,
                                available_input_fields: Optional[Dict] = None) -> Any:
        class InterpretedValue(BaseModel):
            value: str = Field(description="The correctly formatted parameter value as a string.")
            motivation: str = Field(description="Reasoning behind the interpretation.")

        confirmation_note = (
            "This was a CONFIRMATION question: the suggested value was shown to the user and they were asked to approve or correct it. "
            "If the user's answer is an affirmation (e.g. 'yes', 'ok', 'looks good', 'correct'), return the suggested value unchanged. "
            "Only produce a different value if the user explicitly provided one.\n\n"
            if needs_confirmation else ""
        )
        fields_note = (
            "If the user's answer contains placeholders that refer to data from a previous step "
            "(e.g. '<email body>', '[email body]', '[Expense name]', '[Sender]', 'the subject line'), replace them with the correct "
            "expression using the format {{{{output_name.field_name}}}} from the available input fields listed below. "
            "Square-bracket placeholders like [Expense name] are the readable labels shown to the user for those same "
            "live values — map them back to the matching expression. "
            "Never use {{{{$json.*}}}} or any other expression syntax. "
            "Example: '<email body>' or '[email body]' → '{{{{email_object.body}}}}'.\n\n"
            if available_input_fields else ""
        )
        prompt = ChatPromptTemplate.from_messages([
            ("system",
                "You are converting a non-technical user's answer into a correctly formatted automation parameter value. "
                "Use the parameter type and description to produce the right format. "
                f"{confirmation_note}"
                f"{fields_note}"
                "Return only the exact value — no explanation in the value field."),
            ("user",
                "Automation: {automation_description}\n\n"
                "Parameter name: {param_name}\n"
                "Parameter definition: {param_definition}\n"
                "Question asked to user: {question_asked}\n"
                "Suggested value: {inferred_value}\n"
                "Available input fields from previous steps: {available_input_fields}\n"
                "User's answer: {user_input}\n\n"
                "Convert the user's answer to the correct parameter value.")
        ])

        result = safe_invoke(prompt | llm.with_structured_output(InterpretedValue), {
            'automation_description': self.automation.description_of_automation,
            'param_name': param_name,
            'param_definition': json.dumps(param_definition, indent=2),
            'question_asked': question_asked,
            'inferred_value': str(inferred_value) if inferred_value is not None else 'none',
            'available_input_fields': json.dumps(available_input_fields or {}, indent=2),
            'user_input': user_input,
        })

        raw = result.value
        param_type = param_definition.get('type', 'string')
        if param_type in ('mapping', 'object'):
            return _parse_object_str(raw)
        if param_type == 'integer':
            try:
                return int(raw)
            except ValueError:
                return raw
        elif param_type == 'boolean':
            return raw.lower() in ['yes', 'true', '1', 'y']
        elif param_type == 'array':
            return [item.strip() for item in raw.split(',')]
        return raw

    def _validate_expression_references(self, value: Any, param_definition: dict,
                                         output_schemas: dict) -> List[str]:
        if not isinstance(value, str):
            return []
        issues = []
        param_type = param_definition.get('type', 'string')
        for match in re.finditer(r'\{+(\w+)\.(\w+)\}+', value):
            output_name, field_name = match.group(1), match.group(2)
            schema = output_schemas.get(output_name)
            if schema is None:
                issues.append(f"'{output_name}' is not a known output object")
                continue
            properties = schema.get('properties', {})
            if field_name not in properties:
                issues.append(f"'{output_name}.{field_name}' does not exist in the output schema")
                continue
            if param_type in ('integer', 'boolean'):
                field_type = properties[field_name].get('type')
                if field_type != param_type:
                    issues.append(
                        f"Type mismatch: '{output_name}.{field_name}' is {field_type}, "
                        f"but parameter expects {param_type}"
                    )
        return issues

    # ── State machine ─────────────────────────────────────────────────────────

    def check_status(self):
        if self.automation.status == "clarifying":
            if self._initial_description is None:
                self._initial_description = self.automation.description_of_automation
            if self._clarification_rounds >= self._max_clarification_rounds:
                print(f"Max clarification rounds ({self._max_clarification_rounds}) reached, proceeding.")
                self.automation.status = "configuring_tools"
                return
            below_minimum = self._clarification_rounds < self._min_clarification_rounds
            data = self.generate_clarification_question_or_complete(
                self.automation.description_of_automation,
                force_question=below_minimum,
            )
            if data.get("question") != "false":
                self.pending_clarification_question = data.get("question")
            else:
                self.automation.status = "configuring_tools"
            return

        if self.automation.status == "configuring_tools":
            self.select_tools_for_automation()
            return

        if self.automation.status == "configuring_credentials":
            self.configure_credentials()
            return

        if self.automation.status == "configuring_resources":
            self.configure_resources()
            return

        if self.automation.status == "configuring_parameters":
            self.configure_parameters()
            return

        if self.automation.status == "ready_for_aspec":
            print("\n" + "="*60)
            print("ASPEC GENERATION")
            print("="*60)

            validation_issues = validate_automation_for_aspec(self.automation)
            if validation_issues:
                print("❌ Validation failed:")
                for issue in validation_issues:
                    print(f"  • {issue}")
                return

            self.generate_metadata_name()

            try:
                aspec = automation_to_aspec(self.automation, self.toolbox)
                self.aspec = aspec
                print("✅ ASPEC generated")
            except Exception as e:
                print(f"❌ ASPEC generation failed: {e}")
                return

            try:
                import jsonschema
                schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'aspec.schema.json')
                with open(schema_path, 'r') as f:
                    schema = json.load(f)
                jsonschema.validate(instance=aspec, schema=schema)
                print("✅ Schema valid")
            except jsonschema.exceptions.ValidationError as e:
                print(f"❌ Schema validation failed: {e.message}")
            except Exception as e:
                print(f"⚠️  Schema validation error: {e}")

            self.automation.status = "finished"
            path = _save_aspec_to_file(aspec, self.scenario)
            print(f"💾 Saved to {path}")
            log_path = self.save_interaction_log()
            print(f"📋 Interaction log saved to {log_path}")
            print(json.dumps(aspec, indent=2))


# ── Parsing helpers ───────────────────────────────────────────────────────────

def _parse_object_str(raw: str) -> Any:
    """
    Parse a string that should be a JSON object or mapping.
    Handles three LLM output patterns:
      - Valid JSON:          '{"key": "val"}'          → dict
      - Double-encoded JSON: '"{\"key\": \"val\"}"'    → dict (parse twice)
      - Python dict repr:   "{'key': 'val'}"           → dict via ast.literal_eval
    After parsing, normalises single-brace expressions {x.y} → {{x.y}} in string values.
    """
    try:
        parsed = json.loads(raw)
        # LLM sometimes wraps the object in extra quotes → parse again
        if isinstance(parsed, str):
            parsed = json.loads(parsed)
    except (json.JSONDecodeError, TypeError):
        try:
            parsed = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            return raw
    if isinstance(parsed, dict):
        return {k: _normalise_expr(v) if isinstance(v, str) else v for k, v in parsed.items()}
    return parsed


def _normalise_expr(value: str) -> str:
    """Convert single-brace expressions {x.y} to double-brace {{x.y}}."""
    return re.sub(r'(?<!\{)\{(\w+\.\w+)\}(?!\})', r'{{\1}}', value)


# Matches an internal expression token: {{...}} or {...}, capturing the inner path.
_EXPRESSION_TOKEN = re.compile(r'\{{1,2}\s*([^{}]+?)\s*\}{1,2}')

def _expression_label(inner: str) -> str:
    """Turn an expression path into a short, human-readable label (no brackets)."""
    # Prefer an explicit bracket-access key, e.g. row["Expense name"] -> Expense name
    bracket = re.search(r'\[\s*["\']([^"\']+)["\']\s*\]', inner)
    if bracket:
        return bracket.group(1).strip()
    # Otherwise take the last dotted segment, e.g. record_object.properties.Sender -> Sender
    segment = inner.split('.')[-1].strip()
    segment = re.sub(r'\[.*?\]', '', segment).strip()  # drop any trailing [..] index
    return segment


def humanize_expressions(text: str) -> str:
    """
    Replace internal expression syntax with readable bracket labels for display.

    Display-only: the real {{output.field}} expressions remain in the stored
    parameter values (and therefore in the ASPEC). This only changes what the
    participant sees in a question, e.g.
        {{row_object.row["Expense name"]}} -> [Expense name]
        {{record_object.properties.Sender}} -> [Sender]
        {{email_object.body}} -> [email body]
    """
    if not isinstance(text, str) or '{' not in text:
        return text
    return _EXPRESSION_TOKEN.sub(lambda m: f"[{_expression_label(m.group(1))}]", text)


# Matches a readable [Bracket Label] placeholder produced by humanize_expressions.
_BRACKET_PLACEHOLDER = re.compile(r'\[[^\[\]]+\]')

_PLACEHOLDER_NOTE = (
    " (The bracketed parts — like [Expense name] — will be automatically replaced "
    "with the real values when the automation runs.)"
)


def _explain_placeholders(text: str) -> str:
    """Append a fixed, deterministic note when readable [bracket] placeholders are
    present, so participants don't mistake them for literal text to be sent."""
    if isinstance(text, str) and _BRACKET_PLACEHOLDER.search(text):
        return text + _PLACEHOLDER_NOTE
    return text


def _strip_expression_tokens(text: str) -> str:
    """Remove all expression tokens, leaving only the surrounding natural text."""
    return _EXPRESSION_TOKEN.sub('', text) if isinstance(text, str) else text


def _describe_expression(expr: str, available_input_fields: Dict[str, Any]) -> Optional[str]:
    """
    Describe a pure {{output.field...}} reference in plain language, grounded in
    the action catalogue's own output-schema descriptions — no hand-authored label
    table, so any new adapter/output picks up sensible phrasing automatically as
    long as it carries a `description` (which the catalogue already requires).

    e.g. {{send_result.messageId}} with available_input_fields containing
    send_result -> "Result of sending an email message — Unique ID of the sent message"
    """
    match = _EXPRESSION_TOKEN.search(expr)
    if not match:
        return None
    parts = [p.strip() for p in match.group(1).split('.') if p.strip()]
    if not parts:
        return None
    node = available_input_fields.get(parts[0])
    if not node:
        return None
    descriptions = []
    if node.get('description'):
        descriptions.append(node['description'])
    for part in parts[1:]:
        node = (node.get('properties') or {}).get(re.sub(r'\[.*?\]', '', part).strip())
        if not node:
            break
        if node.get('description'):
            descriptions.append(node['description'])
    return " — ".join(descriptions) if descriptions else None


# ── ASPEC persistence ─────────────────────────────────────────────────────────

def _save_aspec_to_file(aspec: dict, scenario: Optional[str]) -> str:
    """Save ASPEC JSON to scenarios/user-aspecs/. Returns the saved path."""
    from datetime import datetime
    script_dir = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(script_dir, "scenarios/user-aspecs")
    os.makedirs(out_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S")
    prefix = f"scenario-{scenario}" if scenario else "scenario-unknown"
    filename = f"{prefix}_{timestamp}.json"
    path = os.path.join(out_dir, filename)
    with open(path, "w") as f:
        json.dump(aspec, f, indent=2)
    return path


# ── CLI helpers ───────────────────────────────────────────────────────────────

def conduct_turn(agent: AjoraAgent, question: str, context: str, max_follow_ups: int = 3) -> str:
    """Ask a question and handle follow-up sub-questions. Returns the user's final answer."""
    current_question = question
    user_input = input(f"\n{current_question}\nYour answer: ")
    for _ in range(max_follow_ups):
        result = agent._classify_dialogue_turn(current_question, user_input, context)
        if result.is_answer:
            return user_input
        print(f"\n{result.follow_up_response}")
        current_question = result.follow_up_question or current_question
        user_input = input(f"\n{current_question}\nYour answer: ")
    return user_input


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Which scenario is this run for?")
    print("  1  Log incoming emails to spreadsheet")
    print("  2  Filter customer emails → Notion + colleague notification")
    print("  3  Expense added to sheet → email finance + label email")
    print("  4  New file in Drive folder → email finance")
    print("  0  Other / free run")
    scenario_input = input("Scenario number: ").strip()
    scenario = scenario_input if scenario_input in {"1", "2", "3", "4"} else "other"

    agent = AjoraAgent()
    agent.scenario = scenario
    agent.automation.description_of_automation = input("\nDescribe your automation: ")

    consecutive_failures = 0
    try:
        while agent.automation.status != "finished":
            print(f"\n[{agent.automation.status}]")
            agent.save_checkpoint()  # persist progress before attempting the step

            try:
                agent.check_status()

                if agent.pending_clarification_question:
                    context = f"Automation so far: {agent.automation.description_of_automation}"
                    answer = conduct_turn(agent, agent.pending_clarification_question, context)
                    agent.refine_automation_description_with_clarification(agent.pending_clarification_question, answer)
                    agent.pending_clarification_question = None

                if agent.pending_missing_credentials:
                    for cred in agent.pending_missing_credentials:
                        print(f"⚠️  Missing credential: {cred['service']} ({cred['auth_type']})")
                    agent.pending_missing_credentials = None

                if agent.pending_missing_resources:
                    for item in agent.pending_missing_resources:
                        print(f"⚠️  Resource not found: {item['resource_type']} for {item['service']}")
                    print("Skipping missing resources and continuing.")
                    agent.pending_missing_resources = None
                    agent.automation.status = "configuring_parameters"

                if agent.pending_resource_selection:
                    for item in agent.pending_resource_selection:
                        print(f"\nMultiple {item['resource_type']}s found for {item['service']}:")
                        for i, option in enumerate(item["options"]):
                            print(f"  {i + 1}. {option['fileName']}")
                        while True:
                            choice = input("Choose a number: ").strip()
                            if choice.isdigit() and 1 <= int(choice) <= len(item["options"]):
                                chosen = item["options"][int(choice) - 1]
                                resource_key = f"{item['service']}_{item['resource_type']}"
                                agent.automation.resources[resource_key] = Resource(
                                    service=item["service"],
                                    type=item["resource_type"],
                                    resource_type=ResourceType(item["resource_type_id"]),
                                    id=chosen["fileId"],
                                    name=chosen["fileName"],
                                    details=chosen.get("details", {}),
                                )
                                agent.log_resource_selection(
                                    item["service"], item["resource_type"], item["options"], chosen
                                )
                                print(f"Selected: {chosen['fileName']}")
                                break
                            print("Invalid choice, try again.")
                    agent.pending_resource_selection = None
                    agent.automation.status = "configuring_parameters"

                if agent.pending_parameter_questions:
                    answers = []
                    for q in agent.pending_parameter_questions:
                        param_def = q.get("param_definition", {})
                        options = param_def.get("options") or param_def.get("enum") or []
                        context = (
                            f"Automation: {agent.automation.description_of_automation}. "
                            f"Parameter: {q['param_name']} (type: {param_def.get('type', 'string')}). "
                            f"Description: {param_def.get('description', '')}. "
                            + (f"Valid options: {options}. " if options else "")
                            + (f"Suggested value: {q.get('inferred_value')}." if q.get('inferred_value') else "")
                        )
                        user_input = conduct_turn(agent, q["question"], context)
                        answers.append({
                            "tool_idx": q["tool_idx"],
                            "param_name": q["param_name"],
                            "user_input": user_input,
                            "question": q.get("question", ""),
                            "inferred_value": q.get("inferred_value"),
                            "param_definition": q.get("param_definition", {}),
                            "needs_confirmation": q.get("needs_confirmation", False),
                            "available_input_fields": q.get("available_input_fields", {}),
                        })
                    agent.apply_parameter_answers(answers)

                consecutive_failures = 0  # a full iteration completed without a filter block

            except ContentPolicyError as cpe:
                consecutive_failures += 1
                ckpt = agent.save_checkpoint()
                print(f"\n⚠️  The content filter rejected that step "
                      f"(attempt {consecutive_failures}/3). Your progress is saved.")
                print(f"   (underlying error, for diagnosis: {cpe})")
                if consecutive_failures < 3:
                    print("Let's try that step again — please rephrase your wording.")
                    # Discard the half-built turn so the step is regenerated cleanly on retry.
                    agent.pending_clarification_question = None
                    agent.pending_parameter_questions = None
                    continue
                # Persistent block: degrade gracefully instead of looping forever.
                print(f"This step keeps being blocked. Checkpoint: {ckpt}")
                if agent.automation.status == "clarifying":
                    print("Skipping further clarification and continuing.")
                    agent.automation.status = "configuring_tools"
                elif agent.pending_parameter_questions:
                    skipped = agent.pending_parameter_questions[0]["param_name"]
                    print(f"Leaving '{skipped}' unset and continuing; flag it during scoring.")
                    agent.pending_parameter_questions = None
                else:
                    print("Unable to continue automatically; ending the run with progress saved.")
                    break
                consecutive_failures = 0
                continue
    except KeyboardInterrupt:
        print(f"\nInterrupted. Progress checkpointed to {agent.save_checkpoint()}")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}\nProgress checkpointed to {agent.save_checkpoint()}")
        raise
