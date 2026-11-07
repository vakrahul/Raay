using System;
using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Threading;
using System.Threading.Tasks;
using WinAI.Client.Models;

namespace WinAI.Client.ViewModels
{
    public class ChatViewModel : INotifyPropertyChanged
    {
        private string _inputMessage = string.Empty;
        private bool _isGenerating = false;
        private CancellationTokenSource? _generationCts;

        public ObservableCollection<ChatMessageModel> Messages { get; } = new();

        public string InputMessage
        {
            get => _inputMessage;
            set { _inputMessage = value; OnPropertyChanged(); }
        }

        public bool IsGenerating
        {
            get => _isGenerating;
            set { _isGenerating = value; OnPropertyChanged(); }
        }

        public event PropertyChangedEventHandler? PropertyChanged;
        protected void OnPropertyChanged([CallerMemberName] string? name = null)
        {
            PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));
        }

        public async Task SendMessageAsync(Func<string, CancellationToken, Task<string>> completionService)
        {
            if (string.IsNullOrWhiteSpace(InputMessage) || IsGenerating)
                return;

            string userPrompt = InputMessage;
            InputMessage = string.Empty;

            Messages.Add(new ChatMessageModel { Role = "user", Content = userPrompt });

            var assistantMsg = new ChatMessageModel
            {
                Role = "assistant",
                Content = string.Empty,
                IsStreaming = true
            };
            Messages.Add(assistantMsg);

            IsGenerating = true;
            _generationCts = new CancellationTokenSource();

            try
            {
                string result = await completionService(userPrompt, _generationCts.Token);
                assistantMsg.Content = result;
            }
            catch (OperationCanceledException)
            {
                assistantMsg.Content += "\n[Generation cancelled by user]";
            }
            finally
            {
                assistantMsg.IsStreaming = false;
                IsGenerating = false;
                _generationCts = null;
            }
        }

        public void CancelGeneration()
        {
            _generationCts?.Cancel();
        }
    }
}
