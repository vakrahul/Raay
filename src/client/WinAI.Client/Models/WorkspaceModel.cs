using System;
using System.Collections.Generic;

namespace WinAI.Client.Models
{
    public class WorkspaceModel
    {
        public string Id { get; set; } = Guid.NewGuid().ToString();
        public string Name { get; set; } = "Default Workspace";
        public string RootPath { get; set; } = string.Empty;
        public string SecurityLevel { get; set; } = "strict";
        public List<string> AllowedTools { get; set; } = new();
        public DateTime CreatedAt { get; set; } = DateTime.UtcNow;
    }
}
