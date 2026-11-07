using System;

namespace WinAI.Client.Models
{
    public class ApprovalRequestModel
    {
        public string RequestId { get; set; } = string.Empty;
        public string Nonce { get; set; } = string.Empty;
        public string ToolName { get; set; } = string.Empty;
        public string TargetResource { get; set; } = string.Empty;
        public string RiskTier { get; set; } = "HIGH";
        public string Reason { get; set; } = string.Empty;
        public DateTime Timestamp { get; set; } = DateTime.UtcNow;
    }
}
