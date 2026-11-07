using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using WinAI.Client.ViewModels;

namespace WinAI.Client.Views
{
    public sealed partial class MainWindow : Window
    {
        public MainViewModel ViewModel { get; } = new();

        public MainWindow()
        {
            this.InitializeComponent();
            ContentFrame.Navigate(typeof(ChatView));
        }

        private void MainNav_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
        {
            if (args.SelectedItem is NavigationViewItem item && item.Tag is string tag)
            {
                ViewModel.NavigateTo(tag);
                switch (tag)
                {
                    case "Chat":
                        ContentFrame.Navigate(typeof(ChatView));
                        break;
                    case "Models":
                    case "Agents":
                    case "Security":
                    case "Memory":
                    default:
                        // Content views mapped per navigation state
                        break;
                }
            }
        }

        private async void EmergencyStop_Click(object sender, RoutedEventArgs e)
        {
            await ViewModel.TriggerEmergencyStopAsync();
        }
    }
}
