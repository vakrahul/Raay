using System;
using System.Net.Http;
using System.Net.WebSockets;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Threading.Tasks;

namespace WinAI.Client.Services
{
    public class IpcService : IDisposable
    {
        private readonly HttpClient _httpClient;
        private readonly Uri _serverUri;
        private ClientWebSocket? _webSocket;

        public IpcService(string baseHost = "127.0.0.1", int port = 8765)
        {
            _serverUri = new Uri($"http://{baseHost}:{port}");
            _httpClient = new HttpClient { BaseAddress = _serverUri };
        }

        public async Task<bool> CheckHealthAsync()
        {
            try
            {
                var response = await _httpClient.GetAsync("/health");
                return response.IsSuccessStatusCode;
            }
            catch
            {
                return false;
            }
        }

        public async Task StreamPromptAsync(
            string prompt,
            Action<string> onToken,
            Action<string, string, string, string> onApprovalNeeded,
            CancellationToken ct)
        {
            _webSocket = new ClientWebSocket();
            var wsUri = new Uri($"ws://{_serverUri.Host}:{_serverUri.Port}/ws/v1/stream");
            await _webSocket.ConnectAsync(wsUri, ct);

            var payload = new
            {
                action = "chat",
                messages = new[] { new { role = "user", content = prompt } }
            };

            var json = JsonSerializer.Serialize(payload);
            var buffer = Encoding.UTF8.GetBytes(json);
            await _webSocket.SendAsync(new ArraySegment<byte>(buffer), WebSocketMessageType.Text, true, ct);

            var receiveBuffer = new byte[8192];
            while (_webSocket.State == WebSocketState.Open && !ct.IsCancellationRequested)
            {
                var result = await _webSocket.ReceiveAsync(new ArraySegment<byte>(receiveBuffer), ct);
                if (result.MessageType == WebSocketMessageType.Close)
                    break;

                var text = Encoding.UTF8.GetString(receiveBuffer, 0, result.Count);
                using var doc = JsonDocument.Parse(text);
                var root = doc.RootElement;

                if (root.TryGetProperty("event", out var evtProp))
                {
                    var evt = evtProp.GetString();
                    if (evt == "token" && root.TryGetProperty("data", out var dataProp))
                    {
                        onToken(dataProp.GetString() ?? string.Empty);
                    }
                    else if (evt == "tool_proposal")
                    {
                        var toolCall = root.GetProperty("tool_call");
                        var policy = root.GetProperty("policy_decision");
                        var decision = policy.GetProperty("decision").GetString();

                        if (decision == "REQUIRE_APPROVAL")
                        {
                            var toolName = toolCall.GetProperty("tool_name").GetString() ?? "";
                            var target = policy.GetProperty("target_resource").GetString() ?? "";
                            var risk = policy.GetProperty("risk_tier").GetString() ?? "HIGH";
                            var nonce = policy.GetProperty("approval_nonce").GetString() ?? "";
                            onApprovalNeeded(toolName, target, risk, nonce);
                        }
                    }
                    else if (evt == "done")
                    {
                        break;
                    }
                }
            }
        }

        public async Task<bool> SendApprovalResponseAsync(string nonce, bool approved)
        {
            var payload = new
            {
                approval_nonce = nonce,
                user_decision = approved ? "APPROVED" : "DENIED"
            };

            var content = new StringContent(JsonSerializer.Serialize(payload), Encoding.UTF8, "application/json");
            var response = await _httpClient.PostAsync("/api/v1/approval/respond", content);
            return response.IsSuccessStatusCode;
        }

        public void Dispose()
        {
            _webSocket?.Dispose();
            _httpClient.Dispose();
        }
    }
}
