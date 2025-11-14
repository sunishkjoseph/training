export class GenAIService {
  constructor(options = {}) {
    this.endpoint = options.endpoint || 'https://generativeai.<region>.oci.oraclecloud.com';
    this.agentId = options.agentId || '<your-agent-id>';
    this.channel = options.channel || 'web';
    this.authHeader = options.authHeader || 'Authorization';
    this.apiKeyProvider = options.apiKeyProvider || (() => Promise.reject(new Error('API key provider not configured')));
  }

  async *streamChatCompletion(prompt, history = []) {
    const apiKey = await this.apiKeyProvider();
    const response = await fetch(`${this.endpoint}/20231130/agents/${this.agentId}/actions/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Accept': 'text/event-stream',
        [this.authHeader]: apiKey
      },
      body: JSON.stringify({
        messages: this._buildPayload(history, prompt),
        channel: this.channel
      })
    });

    if (!response.ok || !response.body) {
      const detail = await response.text();
      throw new Error(`OCI Generative AI request failed: ${detail}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    let sawDelta = false;

    yield { type: 'start' };

    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }

      buffer += decoder.decode(value, { stream: true });
      const events = buffer.split('\n\n');
      buffer = events.pop() || '';

      for (const event of events) {
        const payload = this._parseEventData(event);
        if (!payload) {
          continue;
        }
        if (payload.choices && payload.choices.length) {
          const delta = payload.choices[0].delta?.content || '';
          if (delta) {
            sawDelta = true;
            yield { type: 'delta', content: delta };
          }
        }
        if (payload.usage) {
          yield { type: 'end', meta: payload.usage };
        }
      }
    }

    if (!sawDelta) {
      yield { type: 'delta', content: '[No response received]' };
    }

    yield { type: 'end' };
  }

  _buildPayload(history, prompt) {
    const previous = history.map(({ role, content }) => ({ role, content }));
    return [...previous, { role: 'user', content: prompt }];
  }

  _parseEventData(eventChunk) {
    const lines = eventChunk.split('\n');
    const dataLines = lines
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.replace(/^data:\s*/, '').trim())
      .filter(Boolean);

    if (!dataLines.length) {
      return null;
    }

    const joined = dataLines.join('');
    if (joined === '[DONE]') {
      return null;
    }

    try {
      return JSON.parse(joined);
    } catch (error) {
      console.warn('Unable to parse stream chunk', error, joined);
      return null;
    }
  }
}
