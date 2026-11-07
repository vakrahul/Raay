using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Threading.Tasks;
using WinAI.Client.Models;

namespace WinAI.Client.ViewModels
{
    public class ApprovalViewModel : INotifyPropertyChanged
    {
        private ApprovalRequestModel? _currentRequest;
        private bool _isPromptVisible = false;

        public ObservableCollection<ApprovalRequestModel> PendingApprovals { get; } = new();

        public ApprovalRequestModel? CurrentRequest
        {
            get => _currentRequest;
            set { _currentRequest = value; OnPropertyChanged(); }
        }

        public bool IsPromptVisible
        {
            get => _isPromptVisible;
            set { _isPromptVisible = value; OnPropertyChanged(); }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged([CallerMemberName] string? name = null)
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        }

        public void EnqueueRequest(ApprovalRequestModel request)
        {
            PendingApprovals.Add(request);
            if (CurrentRequest == null)
            {
                CurrentRequest = request;
                IsPromptVisible = true;
            }
        }

        public async Task RespondAsync(bool approve, Func<string, bool, Task> callback)
        {
            if (CurrentRequest == null) return;

            string nonce = CurrentRequest.Nonce;
            PendingApprovals.Remove(CurrentRequest);
            CurrentRequest = PendingApprovals.Count > 0 ? PendingApprovals[0] : null;
            IsPromptVisible = CurrentRequest != null;

            await callback(nonce, approve);
        }
    }
}
