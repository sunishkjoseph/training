import { load as parseYaml } from 'js-yaml';

const DEFAULT_CONFIG_LOCATIONS = [
  { path: 'config/app-config.yaml', parser: parseYaml, format: 'YAML' },
  { path: 'config/app-config.yml', parser: parseYaml, format: 'YAML' },
  { path: 'config/app-config.json', parser: JSON.parse, format: 'JSON' }
];

function ensureObject(value, descriptor) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`${descriptor} is empty or invalid.`);
  }
  return value;
}

async function fetchText(path) {
  const response = await fetch(path, { cache: 'no-store' });
  if (!response.ok) {
    throw new Error(`${response.status} ${response.statusText}`);
  }
  return response.text();
}

export async function loadAppConfig(locations = DEFAULT_CONFIG_LOCATIONS) {
  const attempts = [];

  for (const location of locations) {
    try {
      const text = await fetchText(location.path);
      const trimmed = text.trim();
      if (!trimmed) {
        throw new Error('File is empty.');
      }

      const parsed = ensureObject(location.parser(trimmed), `${location.format} configuration`);
      return parsed;
    } catch (error) {
      attempts.push(`${location.path}: ${error.message}`);
    }
  }

  const details = attempts.join('\n');
  throw new Error(`Unable to load application configuration.\n${details}`);
}

export function normalizeAppConfig(config) {
  const normalized = ensureObject(config, 'Configuration root');

  const service = ensureObject(normalized.service, 'service section');
  if (!service.endpoint) {
    throw new Error('service.endpoint is required.');
  }
  if (!service.agentId) {
    throw new Error('service.agentId is required.');
  }

  const auth = normalized.auth || {};
  const tokenValue = auth.token || auth.value || auth.apiKey || '';

  return {
    service: {
      endpoint: service.endpoint,
      agentId: service.agentId,
      channel: service.channel || 'web'
    },
    auth: {
      header: auth.header || 'Authorization',
      token: typeof tokenValue === 'string' ? tokenValue.trim() : tokenValue
    }
  };
}
