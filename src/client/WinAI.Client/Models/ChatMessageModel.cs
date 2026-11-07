using System;

namespace WinAI.Client.Models
{
    public class ChatMessageModel
    {
        public string Role { get; set; } = "user";
        public string Content { get; set; } = string.Empty;
        public DateTime Timestamp { get; set; } = DateTime.UtcNow;
        public bool IsStreaming { get; set; }
        public bool IsUser => Role.Equals("user", StringComparison.OrdinalIgnoreCase);
        public bool IsAssistant => Role.Equals("assistant", StringComparison.OrdinalIgnoreCase);
        public bool IsTool => Role.Equals("tool", StringComparison.OrdinalIgnoreCase);
    }
}
