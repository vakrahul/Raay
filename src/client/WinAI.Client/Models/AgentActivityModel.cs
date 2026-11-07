using System;

namespace WinAI.Client.Models
{
    public enum AgentStatus
    {
        Idle,
        Planning,
        ExecutingTool,
        AwaitingApproval,
        Completed,
        Failed,
        Terminated
    }

    public class AgentActivityModel
    {
        public string AgentId { get; set; } = string.Empty;
        public string RoleName { get; set; } = "General Assistant";
        public string CurrentTask { get; set; } = string.Empty;
        public AgentStatus Status { get; set; } = AgentStatus.Idle;
        public string LastToolExecuted { get; set; } = string.Empty;
        public DateTime LastUpdated { get; set; } = DateTime.UtcNow;
    }
}
