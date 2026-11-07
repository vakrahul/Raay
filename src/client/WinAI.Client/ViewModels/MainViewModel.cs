using System;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Threading.Tasks;

namespace WinAI.Client.ViewModels
{
    public class MainViewModel : INotifyPropertyChanged
    {
        private string _activeView = "Chat";
        private bool _isEmergencyStopped = false;
        private string _connectionStatus = "Connected to Orchestrator (127.0.0.1:8765)";

        public string ActiveView
        {
            get => _activeView;
            set { _activeView = value; OnPropertyChanged(); }
        }

        public bool IsEmergencyStopped
        {
            get => _isEmergencyStopped;
            set { _isEmergencyStopped = value; OnPropertyChanged(); }
        }

        public string ConnectionStatus
        {
            get => _connectionStatus;
            set { _connectionStatus = value; OnPropertyChanged(); }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged([CallerMemberName] string? name = null)
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        }

        public void NavigateTo(string viewName)
        {
            ActiveView = viewName;
        }

        public async Task TriggerEmergencyStopAsync()
        {
            IsEmergencyStopped = true;
            ConnectionStatus = "EMERGENCY STOP TRIGGERED: All agents paused/revoked";
            await Task.CompletedTask;
        }
    }
}
