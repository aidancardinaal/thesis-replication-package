"""
ASPEC Mapper - Transforms Automation objects to ASPEC format.

This module provides deterministic mapping from the planning-time Automation
object to the execution-time ASPEC (Automation Specification) format.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from classes import Automation, ToolStep


def automation_to_aspec(automation: Automation, toolbox: dict) -> dict:
    """
    Transform a completed Automation object into an ASPEC.
    
    Args:
        automation: The completed Automation object (status should be 'ready_for_aspec')
        toolbox: The toolbox dictionary containing adapter definitions
    
    Returns:
        A dict conforming to the ASPEC schema
    """
    if automation.status != "ready_for_aspec":
        raise ValueError(f"Automation status must be 'ready_for_aspec', got '{automation.status}'")
    
    if not automation.tools.trigger:
        raise ValueError("Automation must have a trigger")
    
    if not automation.tools.actions:
        raise ValueError("Automation must have at least one action")
    
    # Build the ASPEC
    aspec = {
        "aspec_version": "1.0",
        "metadata": _build_metadata(automation),
        "credentials": _build_credentials(automation),
        "resources": _build_resources(automation),
        "trigger": _build_step(
            automation.tools.trigger, 
            "trigger_1", 
            toolbox,
            automation,
            is_trigger=True
        ),
        "steps": _build_action_steps(automation, toolbox),
        "connections": _build_connections(automation)
    }
    
    return aspec


def _build_metadata(automation: Automation) -> dict:
    """Build the metadata section of the ASPEC."""
    description = automation.description_of_automation
    name = automation.metadata_name or (description[:50] + "..." if len(description) > 50 else description)

    return {
        "name": name,
        "description": description,
        "created_at": datetime.utcnow().isoformat() + "Z",
        "tags": []
    }


def _build_credentials(automation: Automation) -> dict:
    """Build the credentials section of the ASPEC."""
    credentials = {}
    
    for key, credential in automation.credentials.items():
        credentials[key] = {
            "auth_type": credential.auth_type,
            "service": credential.service,
            "credential_id": credential.credential_id,
            "status": credential.status
        }
    
    return credentials


def _build_resources(automation: Automation) -> dict:
    """Build the resources section of the ASPEC."""
    resources = {}
    
    for key, resource in automation.resources.items():
        resources[key] = {
            "service": resource.service,
            "type": resource.type,
            "id": resource.id,
            "name": resource.name,
            "details": resource.details
        }
    
    return resources


def _build_step(
    tool_step: ToolStep, 
    step_id: str, 
    toolbox: dict,
    automation: Automation,
    is_trigger: bool = False,
    input_from: Optional[str] = None
) -> dict:
    """Build a single step for the ASPEC."""
    # Lookup adapter in toolbox for additional info
    adapter = _get_adapter(toolbox, tool_step.adapter_id)
    
    step = {
        "step_id": step_id,
        "adapter_id": tool_step.adapter_id,
        "adapter_name": tool_step.adapter_name,
        "service": tool_step.service,
        "is_trigger": is_trigger,
        "configured_parameters": tool_step.configured_parameters,
        "parameter_origin": _build_parameter_origin(tool_step, adapter)
    }
    
    # Add credential reference if this step's service has a configured credential
    credential_ref = _find_credential_for_service(automation, tool_step.service)
    if credential_ref:
        step["credential_ref"] = credential_ref
    
    # Add trigger_mode for triggers
    if is_trigger and adapter:
        trigger_mode = adapter.get("trigger_mode")
        if trigger_mode:
            step["trigger_mode"] = trigger_mode
    
    # Add input/output info from toolbox
    if adapter:
        outputs = adapter.get("outputs", [])

        if outputs:
            step["outputs"] = outputs
    
    # Add input_from for non-trigger steps
    if input_from:
        step["input_from"] = input_from
    
    return step


def _build_action_steps(automation: Automation, toolbox: dict) -> List[dict]:
    """Build the steps array for actions."""
    steps = []
    previous_step_id = "trigger_1"
    
    for i, action in enumerate(automation.tools.actions):
        step_id = f"step_{i + 1}"
        
        input_from = previous_step_id
        
        step = _build_step(
            action,
            step_id,
            toolbox,
            automation,
            is_trigger=False,
            input_from=input_from
        )
        steps.append(step)
        previous_step_id = step_id
    
    return steps


def _build_connections(automation: Automation) -> List[dict]:
    """Build the connections array showing data flow."""
    connections = []
    
    # Trigger -> first action
    if automation.tools.actions:
        connections.append({
            "from": "trigger_1",
            "to": "step_1",
            "type": "main"
        })
    
    # Each action -> next action
    for i in range(len(automation.tools.actions) - 1):
        connections.append({
            "from": f"step_{i + 1}",
            "to": f"step_{i + 2}",
            "type": "main"
        })
    
    return connections


def _build_parameter_origin(tool_step: ToolStep, adapter: Optional[dict]) -> dict:
    """
    Return the parameter_origin dict tracked during pipeline execution.
    Falls back to 'user' for any parameter not explicitly tagged (should not occur).
    """
    origins = {}
    for param_name in tool_step.configured_parameters.keys():
        origins[param_name] = tool_step.parameter_origin.get(param_name, "user")
    return origins


def _get_adapter(toolbox: dict, adapter_id: str) -> Optional[dict]:
    """Lookup an adapter in the toolbox by ID."""
    for adapter in toolbox.get("adapters", []):
        if adapter["id"] == adapter_id:
            return adapter
    return None




def _find_credential_for_service(automation: Automation, service: str) -> Optional[str]:
    """
    Find the credential key that matches the given service.
    
    Returns the credential key (e.g., 'gmail_cred') or None if not found.
    """
    for cred_key, credential in automation.credentials.items():
        if credential.service == service:
            return cred_key
    return None


# Convenience function for validation
def validate_automation_for_aspec(automation: Automation) -> List[str]:
    """
    Validate that an Automation object is ready to be converted to ASPEC.
    
    Returns a list of issues. Empty list means validation passed.
    """
    issues = []
    
    if not automation.tools.trigger:
        issues.append("No trigger configured")
    elif automation.tools.trigger.missing_parameters:
        issues.append(f"Trigger has missing parameters: {automation.tools.trigger.missing_parameters}")
    
    if not automation.tools.actions:
        issues.append("No actions configured")
    else:
        for i, action in enumerate(automation.tools.actions):
            if action.missing_parameters:
                issues.append(f"Action[{i}] has missing parameters: {action.missing_parameters}")
    
    for cred_key, cred in automation.credentials.items():
        if cred.status != "ok":
            issues.append(f"Credential '{cred_key}' status is '{cred.status}'")
    
    return issues
