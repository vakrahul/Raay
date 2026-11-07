using System;

namespace WinAI.Client.Models
{
    public class PermissionModel
    {
        public string PermissionKey { get; set; } = string.Empty;
        public string Description { get; set; } = string.Empty;
        public bool IsGranted { get; set; } = false;
        public bool RequiresApproval { get; set; } = true;
        public string Scope { get; set; } = "workspace";
    }
}
