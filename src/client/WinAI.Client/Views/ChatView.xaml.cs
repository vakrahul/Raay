using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using System.Threading;
using System.Threading.Tasks;
using WinAI.Client.ViewModels;

namespace WinAI.Client.Views
{
    public sealed partial class ChatView : Page
    {
        public ChatViewModel ViewModel { get; } = new();

        public ChatView()
        {
            this.InitializeComponent();
        }

        private async void Send_Click(object sender, RoutedEventArgs e)
        {
            await ViewModel.SendMessageAsync(async (prompt, cancellationToken) =>
            {
                // Simulated or IPC bridge completion
                await Task.Delay(500, cancellationToken);
                return $"Echo response for: '{prompt}'";
            });
        }

        private void Cancel_Click(object sender, RoutedEventArgs e)
        {
            ViewModel.CancelGeneration();
        }
    }
}
