using System;

namespace WinAI.Client.Models
{
    public class ProviderConnectionModel
    {
        public string Id { get; set; } = Guid.NewGuid().ToString();
        public string ProviderName { get; set; } = "OpenAI";
        public string EndpointUrl { get; set; } = "https://api.openai.com/v1";
        public string SelectedModel { get; set; } = "gpt-4o";
        public bool IsActive { get; set; } = true;
        public bool IsLocal { get; set; } = false;
        public bool HasCredentialStored { get; set; } = false;
    }
}
