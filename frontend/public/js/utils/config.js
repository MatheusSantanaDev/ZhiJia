let appConfig = null;

export async function loadConfig() {
    if (appConfig) return appConfig;

    try {
        const response = await fetch('./config.json');
        if (!response.ok) {
            throw new Error(`Failed to load config.json: ${response.status}`);
        }
        appConfig = await response.json();
        return appConfig;
    } catch (error) {
        console.error('[Config] Erro ao carregar config.json:', error);
        throw error;
    }
}

export function getConfig() {
    if (!appConfig) {
        throw new Error('[Config] Config não carregado. Chame loadConfig() primeiro.');
    }
    return appConfig;
}

export function getConfigValue(key, defaultValue = null) {
    if (!appConfig) return defaultValue;
    return appConfig[key] ?? defaultValue;
}