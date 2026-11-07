"""Strict action validation and sanitization engine (Phase 56)."""
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field, ValidationError


SHELL_INJECTION_PATTERN = re.compile(r"[;&|`$><]")


class FsReadFileSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    max_bytes: Optional[int] = Field(default=1048576, ge=1, le=10485760)


class FsWriteFileSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=1)
    content: str
    mode: Optional[str] = Field(default="overwrite", pattern="^(overwrite|append)$")


class CmdExecSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    command: List[str] = Field(min_length=1)  # Argument array only!
    timeout_seconds: Optional[int] = Field(default=60, ge=1, le=300)


class AppLaunchSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    app_id: str = Field(min_length=1)
    extra_args: Optional[List[str]] = Field(default_factory=list)


class WindowFindSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    class_name: Optional[str] = None
    timeout_seconds: Optional[float] = Field(default=5.0, ge=0.1, le=60.0)


class WindowFocusSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    timeout_seconds: Optional[float] = Field(default=3.0, ge=0.1, le=30.0)
    hwnd: Optional[int] = Field(default=None, ge=1)


class WindowTypeTextSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    text: str
    automation_id: Optional[str] = None
    name: Optional[str] = None
    control_type: Optional[str] = None
    clear_first: Optional[bool] = False


class WindowSendKeysSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    keys: str = Field(min_length=1)
    wait_time: Optional[float] = Field(default=0.05, ge=0.0, le=5.0)
    hwnd: Optional[int] = Field(default=None, ge=1)


class WindowClickControlSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    name: Optional[str] = None
    control_type: Optional[str] = None
    automation_id: Optional[str] = None


class WindowInvokeControlSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    name: Optional[str] = None
    automation_id: Optional[str] = None


class WindowSelectMenuSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    menu_path: str = Field(min_length=1)


class WindowWaitForControlSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    name: Optional[str] = None
    control_type: Optional[str] = None
    automation_id: Optional[str] = None
    timeout_seconds: Optional[float] = Field(default=5.0, ge=0.1, le=60.0)


class SystemGetOverviewSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detailed: Optional[bool] = False


class SystemGetMemoryStatusSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    detailed: Optional[bool] = False


class ProcessListSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sort_by: Optional[str] = Field(default="memory", pattern="^(memory|cpu|name|pid)$")
    limit: Optional[int] = Field(default=20, ge=1, le=200)


class ProcessGetTopConsumersSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metric: Optional[str] = Field(default="memory", pattern="^(memory|cpu)$")
    limit: Optional[int] = Field(default=10, ge=1, le=50)


class ProcessGetInfoSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: int = Field(ge=0)


class ProcessCloseSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pid: Optional[int] = Field(default=None, ge=0)
    app_name: Optional[str] = None
    force: Optional[bool] = False


class WindowListSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    visible_only: Optional[bool] = True


class BrowserSelectTabSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    window_title: str = Field(min_length=1)
    tab_name: str = Field(min_length=1)
    hwnd: Optional[int] = Field(default=None, ge=1)


class WindowGetForegroundSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ActionValidator:
    """Validates raw tool call proposals against strict schemas and checks for injection."""

    SCHEMAS = {
        "fs_read_file": FsReadFileSchema,
        "fs_list_files": FsReadFileSchema,
        "fs_write_file": FsWriteFileSchema,
        "terminal_run": CmdExecSchema,
        "cmd_exec": CmdExecSchema,
        "app_launch": AppLaunchSchema,
        "window_find": WindowFindSchema,
        "window_focus": WindowFocusSchema,
        "window_type_text": WindowTypeTextSchema,
        "window_send_keys": WindowSendKeysSchema,
        "window_click_control": WindowClickControlSchema,
        "window_invoke_control": WindowInvokeControlSchema,
        "window_select_menu": WindowSelectMenuSchema,
        "window_wait_for_control": WindowWaitForControlSchema,
        "system_get_overview": SystemGetOverviewSchema,
        "system_get_memory_status": SystemGetMemoryStatusSchema,
        "process_list": ProcessListSchema,
        "process_get_top_consumers": ProcessGetTopConsumersSchema,
        "process_get_info": ProcessGetInfoSchema,
        "process_close": ProcessCloseSchema,
        "window_list": WindowListSchema,
        "window_get_foreground": WindowGetForegroundSchema,
        "browser_get_url": WindowFocusSchema,
        "browser_get_heading": WindowFocusSchema,
        "window_get_text": WindowClickControlSchema,
        "browser_select_tab": BrowserSelectTabSchema,
    }

    @classmethod
    def validate_action(cls, tool_name: str, arguments: Dict[str, Any]) -> Tuple[bool, Optional[BaseModel], str]:
        """Validate tool invocation arguments against strict schema and injection sanitizers."""
        if tool_name not in cls.SCHEMAS:
            return False, None, f"Tool '{tool_name}' is not recognized or has no validation schema"

        # Check for null bytes in any string value
        for k, v in arguments.items():
            if isinstance(v, str) and "\x00" in v:
                return False, None, f"Security Violation: Null byte detected in parameter '{k}'"

        schema_cls = cls.SCHEMAS[tool_name]
        try:
            validated = schema_cls(**arguments)
        except ValidationError as e:
            return False, None, f"Schema validation failure: {e}"

        # If it's a command execution, ensure no raw shell strings
        if isinstance(validated, CmdExecSchema):
            for arg in validated.command:
                if "\x00" in arg:
                    return False, None, "Null byte in command argument"

        return True, validated, "Validation successful"

