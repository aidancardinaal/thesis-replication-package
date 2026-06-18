from datetime import datetime
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field
from enum import Enum


class Credential(BaseModel):
    status: Literal["unknown", "ok", "missing", "invalid"] = Field(
        description="Current credential status."
    )
    service: str = Field(
        description="Service identifier (e.g. 'gmail', 'google_sheets', 'outlook')."
    )
    auth_type: str = Field(
        description="Authentication type identifier (e.g. 'gmailOAuth2', 'googleSheetsOAuth2Api')."
    )
    credential_id: Optional[str] = Field(
        default=None,
        description="Identifier of the credential in your system (if any).",
    )
    last_checked_at: Optional[datetime] = Field(
        default=None,
        description="When this credential status was last checked.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional notes about why the credential is missing/invalid, etc.",
    )





class ResourceType(str, Enum):
    """Resource type identifiers used to match vault resources against adapter requirements."""
    # Documents
    PDF = "application/pdf"
    JSON = "application/json"
    XML = "application/xml"
    CSV = "text/csv"
    PLAIN_TEXT = "text/plain"
    HTML = "text/html"
    MARKDOWN = "text/markdown"

    # Microsoft Office
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

    # Google Workspace
    GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
    GOOGLE_DOC = "application/vnd.google-apps.document"
    GOOGLE_SLIDES = "application/vnd.google-apps.presentation"
    GOOGLE_FOLDER = "application/vnd.google-apps.folder"

    # Notion
    NOTION_DATABASE = "application/vnd.notion.database"
    NOTION_PAGE = "application/vnd.notion.page"

    # Images
    PNG = "image/png"
    JPEG = "image/jpeg"
    GIF = "image/gif"
    SVG = "image/svg+xml"

    # Email
    EMAIL = "message/rfc822"

    # Other
    UNKNOWN = "application/octet-stream"

    @classmethod
    def get_all_types(cls) -> Dict[str, str]:
        return {member.name: member.value for member in cls}

    @classmethod
    def get_by_name(cls, name: str) -> Optional["ResourceType"]:
        try:
            return cls[name]
        except KeyError:
            return None


class Resource(BaseModel):
    service: str = Field(
        description="Service identifier (e.g. 'gmail', 'google_sheets', 'outlook')."
    )
    type: Optional[str] = Field(
        default=None,
        description="Resource type within the service (e.g. 'account', 'spreadsheet', 'sheet_tab', 'folder').",
    )
    resource_type: ResourceType = Field(
        default=None,
        description="Resource type identifier used to match this resource against adapter requirements.",
    )
    id: Optional[str] = Field(
        default=None,
        description="Service-specific ID of this resource (e.g. spreadsheetId, mailboxId).",
    )
    name: Optional[str] = Field(
        default=None,
        description="Human-readable name of the resource (e.g. 'mails', 'Inbox').",
    )
    details: Dict[str, Any] = Field(
        default_factory=dict,
        description="Optional additional metadata (e.g. columns, sheet names, folder paths).",
    )


class ToolStep(BaseModel):
    domain: str = Field(
        description="High-level domain of the tool (e.g. 'email', 'spreadsheet', 'calendar')."
    )
    action_id: str = Field(
        description="ID of the action in the toolbox (e.g. 'email.trigger', 'spreadsheet.append')."
    )
    adapter_id: str = Field(
        description="ID of the adapter in the toolbox (e.g. 'email.trigger.gmail')."
    )
    adapter_name: Optional[str] = Field(
        default=None,
        description="Human-readable name of the adapter (e.g. 'Gmail Trigger').",
    )
    service: Optional[str] = Field(
        default=None,
        description="Service used by this adapter (e.g. 'gmail', 'google_sheets').",
    )
    is_trigger: bool = Field(
        default=False,
        description="True if this planned step is intended as the automation trigger.",
    )
    configured_parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Parameters that currently have a value (from user, defaults, or inference).",
    )
    parameter_origin: Dict[str, str] = Field(
        default_factory=dict,
        description="Origin of each configured parameter: 'user', 'inferred', 'default', or 'system'.",
    )
    missing_parameters: List[str] = Field(
        default_factory=list,
        description="Names of parameters that are still unclear and need clarification.",
    )
    notes: Optional[str] = Field(
        default=None,
        description="Optional notes or reasoning about this tool choice (for debugging or UX).",
    )


class Tools(BaseModel):
    trigger: Optional[ToolStep] = Field(
        default=None,
        description="The planned trigger tool (may be partially configured while in planning).",
    )
    actions: List[ToolStep] = Field(
        default_factory=list,
        description="Planned action tools (may be partially configured while in planning).",
    )


class Automation(BaseModel):
    description_of_automation: str = Field(
        description="A description of the automation task in natural language."
    )
    status: Literal["clarifying", "configuring_tools", "configuring_credentials", "configuring_resources", "configuring_parameters", "ready_for_aspec", "finished", "error"] = Field(
        description="Current planning status of the automation."
    )
    tools: Tools = Field(
        description="Current view of which tools (trigger and actions) are selected and how far they are configured."
    )
    resources: Dict[str, Resource] = Field(
        default_factory=dict,
        description="Resolved external resources used by the automation (mailboxes, spreadsheets, folders, etc.).",
    )
    credentials: Dict[str, Credential] = Field(
        default_factory=dict,
        description="Credential per service used in this automation.",
    )
    metadata_name: Optional[str] = Field(
        default=None,
        description="LLM-generated short name for the automation, set just before ASPEC generation.",
    )
    conversation_history: str = Field(
        default="",
        description="History of the conversation with the user during planning.",
    )
