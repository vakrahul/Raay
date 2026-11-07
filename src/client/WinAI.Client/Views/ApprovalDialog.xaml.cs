using Microsoft.UI.Xaml.Controls;

namespace WinAI.Client.Views
{
    public sealed partial class ApprovalDialog : ContentDialog
    {
        public string ToolName { get; set; } = string.Empty;
        public string RiskTier { get; set; } = "HIGH";
        public string TargetResource { get; set; } = string.Empty;
        public string Reason { get; set; } = string.Empty;
        public string Nonce { get; set; } = string.Empty;

        public ApprovalDialog()
        {
            this.InitializeComponent();
        }
    }
}
