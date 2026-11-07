"""Unit tests validating Stage II Windows Desktop client architecture and contracts."""
import xml.etree.ElementTree as ET
from pathlib import Path
import pytest


@pytest.mark.unit
def test_client_project_file_validity():
    """Verify that WinAI.Client.csproj is valid XML and targets net8.0-windows."""
    proj_path = Path("src/client/WinAI.Client/WinAI.Client.csproj").resolve()
    assert proj_path.exists(), "WinAI.Client.csproj must exist"
    tree = ET.parse(proj_path)
    root = tree.getroot()
    target_framework = root.find(".//TargetFramework")
    assert target_framework is not None
    assert "net8.0-windows" in target_framework.text


@pytest.mark.unit
def test_app_manifest_dpi_awareness():
    """Verify app.manifest configures PerMonitorV2 DPI awareness for accessible rendering."""
    manifest_path = Path("src/client/WinAI.Client/app.manifest").resolve()
    assert manifest_path.exists()
    tree = ET.parse(manifest_path)
    root = tree.getroot()
    # Check that dpiAwareness element exists in XML
    content = manifest_path.read_text(encoding="utf-8")
    assert "PerMonitorV2" in content


@pytest.mark.unit
def test_client_views_and_viewmodels_scaffolded():
    """Verify presence of core MVVM files for Stage II phases 11-20."""
    expected_files = [
        "src/client/WinAI.Client/Views/MainWindow.xaml",
        "src/client/WinAI.Client/Views/MainWindow.xaml.cs",
        "src/client/WinAI.Client/Views/ChatView.xaml",
        "src/client/WinAI.Client/Views/ChatView.xaml.cs",
        "src/client/WinAI.Client/Views/ApprovalDialog.xaml",
        "src/client/WinAI.Client/Views/ApprovalDialog.xaml.cs",
        "src/client/WinAI.Client/ViewModels/MainViewModel.cs",
        "src/client/WinAI.Client/ViewModels/ChatViewModel.cs",
        "src/client/WinAI.Client/ViewModels/ApprovalViewModel.cs",
        "src/client/WinAI.Client/Services/IpcService.cs",
        "src/client/WinAI.Client/Models/ChatMessageModel.cs",
        "src/client/WinAI.Client/Models/ApprovalRequestModel.cs",
        "src/client/WinAI.Client/Models/ProviderConnectionModel.cs",
        "src/client/WinAI.Client/Models/WorkspaceModel.cs",
        "src/client/WinAI.Client/Models/AgentActivityModel.cs",
        "src/client/WinAI.Client/Models/PermissionModel.cs",
    ]
    for rel_path in expected_files:
        p = Path(rel_path).resolve()
        assert p.exists(), f"Expected client file missing: {rel_path}"
