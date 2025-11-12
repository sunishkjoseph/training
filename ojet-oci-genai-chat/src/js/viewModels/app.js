import * as ko from 'knockout';
import { GenAIService } from '../service/GenAIService';
import { loadAppConfig, normalizeAppConfig } from '../config/ConfigLoader';

class AppViewModel {
  constructor() {
    this.prompt = ko.observable('');
    this.conversation = ko.observableArray([]);
    this.messages = ko.observableArray([]);
    this.isStreaming = ko.observable(false);
    this.isReady = ko.observable(false);
    this.statusMessage = ko.observable('Loading configuration...');

    this.service = null;
    this.disableSend = ko.pureComputed(() => !this.prompt() || this.isStreaming() || !this.isReady());

    this._initialize();
  }

  roleLabel(role) {
    return role === 'agent' ? 'Agent' : 'You';
  }

  handleInputChange = () => {
    this.messages([]);
  };

  handleSend = async () => {
    if (this.disableSend()) {
      return;
    }

    const prompt = this.prompt().trim();
    if (!prompt) {
      this.messages([{ severity: 'warning', summary: 'Prompt required', detail: 'Type a question before sending.' }]);
      return;
    }

    if (!this.service) {
      this.messages([{ severity: 'error', summary: 'Configuration missing', detail: 'The chat service is not ready yet.' }]);
      return;
    }

    this._appendConversation({ role: 'user', content: prompt });
    this.prompt('');

    const history = this.conversation()
      .slice(0, -1)
      .map(({ role, content }) => ({ role, content }));

    try {
      this.isStreaming(true);
      for await (const chunk of this.service.streamChatCompletion(prompt, history)) {
        if (chunk.type === 'start') {
          this._appendConversation({ role: 'agent', content: '' });
        } else if (chunk.type === 'delta') {
          this._extendLatestAgentMessage(chunk.content || '');
        } else if (chunk.type === 'end' && chunk.meta) {
          this._setLatestAgentMeta(chunk.meta);
        }
      }
    } catch (error) {
      const detail = error?.message || 'Unexpected error calling OCI Generative AI.';
      this.messages([{ severity: 'error', summary: 'Chat failed', detail }]);
    } finally {
      this.isStreaming(false);
    }
  };

  handleReset = () => {
    this.conversation([]);
    this.messages([]);
  };

  _appendConversation(message) {
    const roleLabel = this.roleLabel(message.role);
    this.conversation.push({ ...message, roleLabel });
  }

  _extendLatestAgentMessage(content) {
    const items = this.conversation();
    if (!items.length) {
      return;
    }
    const lastIndex = items.length - 1;
    const last = items[lastIndex];
    if (last.role !== 'agent') {
      return;
    }
    items[lastIndex] = { ...last, content: `${last.content}${content}` };
    this.conversation.valueHasMutated();
  }

  _setLatestAgentMeta(meta) {
    const items = this.conversation();
    if (!items.length) {
      return;
    }
    const lastIndex = items.length - 1;
    const last = items[lastIndex];
    if (last.role !== 'agent') {
      return;
    }
    items[lastIndex] = { ...last, meta };
    this.conversation.valueHasMutated();
  }

  async _initialize() {
    try {
      const rawConfig = await loadAppConfig();
      const config = normalizeAppConfig(rawConfig);
      this.service = new GenAIService({
        endpoint: config.service.endpoint,
        agentId: config.service.agentId,
        channel: config.service.channel,
        authHeader: config.auth.header,
        apiKeyProvider: this._buildApiKeyProvider(config.auth)
      });
      this.statusMessage('Configuration loaded. You can begin chatting.');
      this.isReady(true);
    } catch (error) {
      const detail = error?.message || 'Unable to load configuration.';
      this.statusMessage('Configuration failed to load.');
      this.messages([{ severity: 'error', summary: 'Configuration error', detail }]);
    }
  }

  _buildApiKeyProvider(authConfig = {}) {
    if (!authConfig.token) {
      return () => Promise.reject(new Error('No authentication token provided in configuration.'));
    }
    return async () => authConfig.token;
  }
}

export = AppViewModel;
