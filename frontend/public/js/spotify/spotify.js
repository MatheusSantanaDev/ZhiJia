import { loadConfig, getConfigValue } from '../utils/config.js';

let spotifyAccessToken = null;
let spotifyRefreshInterval = null;
let spotifyConfig = null;

export async function initSpotifyConfig() {
    await loadConfig();
    spotifyConfig = {
        clientId: getConfigValue('spotify_client_id'),
        redirectUri: getConfigValue('spotify_redirect_uri', window.location.origin + '/'),
        scopes: getConfigValue('spotify_scopes', [
            'user-read-playback-state',
            'user-modify-playback-state',
            'user-read-currently-playing'
        ]).join(' ')
    };

    if (!spotifyConfig.clientId || spotifyConfig.clientId === 'SEU_SPOTIFY_CLIENT_ID') {
        console.warn('[Spotify] Client ID não configurado em config.json');
    }

    return spotifyConfig;
}

function generateRandomString(length) {
    const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    const values = crypto.getRandomValues(new Uint8Array(length));
    return values.reduce((acc, x) => acc + possible[x % possible.length], '');
}

async function generateCodeChallenge(codeVerifier) {
    const data = new TextEncoder().encode(codeVerifier);
    const digest = await crypto.subtle.digest('SHA-256', data);
    return btoa(String.fromCharCode(...new Uint8Array(digest)))
        .replace(/=/g, '')
        .replace(/\+/g, '-')
        .replace(/\//g, '_');
}

export async function spotifyLogin() {
    if (!spotifyConfig) await initSpotifyConfig();

    const codeVerifier = generateRandomString(64);
    localStorage.setItem('spotify_code_verifier', codeVerifier);

    const codeChallenge = await generateCodeChallenge(codeVerifier);

    const params = new URLSearchParams({
        client_id: spotifyConfig.clientId,
        response_type: 'code',
        redirect_uri: spotifyConfig.redirectUri,
        code_challenge_method: 'S256',
        code_challenge: codeChallenge,
        scope: spotifyConfig.scopes
    });

    window.location.href = `https://accounts.spotify.com/authorize?${params.toString()}`;
}

async function exchangeCodeForToken(code) {
    if (!spotifyConfig) await initSpotifyConfig();

    const codeVerifier = localStorage.getItem('spotify_code_verifier');
    if (!codeVerifier) {
        console.error('[Spotify] code_verifier não encontrado no localStorage');
        return;
    }

    console.log('[Spotify] Trocando código por token...', { code: code.substring(0, 10) + '...' });

    try {
        const response = await fetch('https://accounts.spotify.com/api/token', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: new URLSearchParams({
                client_id: spotifyConfig.clientId,
                grant_type: 'authorization_code',
                code: code,
                redirect_uri: spotifyConfig.redirectUri,
                code_verifier: codeVerifier
            })
        });

        const data = await response.json();

        console.log('[Spotify] Resposta do token:', { 
            ok: response.ok, 
            status: response.status,
            hasAccessToken: !!data.access_token,
            error: data.error,
            errorDescription: data.error_description
        });

        if (!response.ok) {
            console.error('[Spotify] Erro ao trocar código:', data.error, data.error_description);
            alert(`Erro Spotify: ${data.error_description || data.error}`);
            return;
        }

        if (data.access_token) {
            spotifyAccessToken = data.access_token;
            localStorage.setItem('spotify_access_token', data.access_token);
            localStorage.setItem('spotify_refresh_token', data.refresh_token);
            localStorage.setItem('spotify_token_expiry', Date.now() + (data.expires_in * 1000));
            localStorage.removeItem('spotify_code_verifier');

            console.log('[Spotify] Token obtido com sucesso');
            showSpotifyPlayer();
            await updateSpotifyNowPlaying();
            startSpotifyRefresh();
        }
    } catch (error) {
        console.error('[Spotify] Exceção ao trocar código:', error);
        alert('Erro de rede ao conectar com Spotify');
    }
}

async function refreshSpotifyToken() {
    if (!spotifyConfig) await initSpotifyConfig();

    const refreshToken = localStorage.getItem('spotify_refresh_token');
    if (!refreshToken) return;

    try {
        const response = await fetch('https://accounts.spotify.com/api/token', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/x-www-form-urlencoded'
            },
            body: new URLSearchParams({
                client_id: spotifyConfig.clientId,
                grant_type: 'refresh_token',
                refresh_token: refreshToken
            })
        });

        const data = await response.json();

        if (!response.ok) {
            console.error('[Spotify] Erro ao renovar token:', data.error, data.error_description);
            return;
        }

        if (data.access_token) {
            spotifyAccessToken = data.access_token;
            localStorage.setItem('spotify_access_token', data.access_token);
            localStorage.setItem('spotify_token_expiry', Date.now() + (data.expires_in * 1000));

            if (data.refresh_token) {
                localStorage.setItem('spotify_refresh_token', data.refresh_token);
            }
            console.log('[Spotify] Token renovado');
        }
    } catch (error) {
        console.error('[Spotify] Exceção ao renovar token:', error);
    }
}

function startSpotifyRefresh() {
    if (spotifyRefreshInterval) clearInterval(spotifyRefreshInterval);
    spotifyRefreshInterval = setInterval(refreshSpotifyToken, 50 * 60 * 1000);
}

function showSpotifyPlayer() {
    document.getElementById('spotifyNotConnected').style.display = 'none';
    document.getElementById('spotifyPlayer').style.display = 'block';
}

async function spotifyApi(endpoint, method = 'GET', body = null) {
    if (!spotifyAccessToken) return null;

    const options = {
        method,
        headers: {
            'Authorization': `Bearer ${spotifyAccessToken}`
        }
    };

    if (body) {
        options.headers['Content-Type'] = 'application/json';
        options.body = JSON.stringify(body);
    }

    const response = await fetch(`https://api.spotify.com/v1${endpoint}`, options);

    if (response.status === 401) {
        await refreshSpotifyToken();
        return spotifyApi(endpoint, method, body);
    }

    // Endpoints que retornam 200/204 sem body em sucesso (comandos de controle)
    const emptySuccessEndpoints = [
        '/me/player/play',
        '/me/player/pause',
        '/me/player/next',
        '/me/player/previous',
        '/me/player/volume',
        '/me/player'
    ];
    
    const isEmptySuccess = emptySuccessEndpoints.some(e => endpoint === e) && 
                          (method === 'PUT' || method === 'POST');

    if (isEmptySuccess && (response.status === 204 || response.status === 200)) {
        return { success: true };
    }

    const contentType = response.headers.get('content-type');
    const text = await response.text();

    let data = {};
    if (text && contentType && contentType.includes('application/json')) {
        try {
            data = JSON.parse(text);
        } catch (e) {
            console.error('[Spotify] Falha ao parsear JSON:', text.substring(0, 200));
        }
    } else if (text) {
        console.warn(`[Spotify] Status ${response.status} - Resposta sem content-type JSON:`, text.substring(0, 200));
    }

    if (!response.ok) {
        console.error(`[Spotify] API error ${response.status}:`, data.error || data);
        return null;
    }

    return data;
}

async function updateSpotifyNowPlaying() {
    const data = await spotifyApi('/me/player/currently-playing');

    if (data && data.item) {
        document.getElementById('spotifyTrackName').textContent = data.item.name;
        document.getElementById('spotifyArtistName').textContent = data.item.artists.map(a => a.name).join(', ');

        if (data.item.album.images.length > 0) {
            document.getElementById('spotifyAlbumArt').src = data.item.album.images[0].url;
        }

        const isPlaying = data.is_playing;
        document.getElementById('spotifyPlayIcon').style.display = isPlaying ? 'none' : 'block';
        document.getElementById('spotifyPauseIcon').style.display = isPlaying ? 'block' : 'none';
    } else if (data === null || (data && !data.item)) {
        // Nenhuma música tocando no momento
        document.getElementById('spotifyTrackName').textContent = 'Nada tocando';
        document.getElementById('spotifyArtistName').textContent = '';
        document.getElementById('spotifyAlbumArt').src = '';
        document.getElementById('spotifyPlayIcon').style.display = 'block';
        document.getElementById('spotifyPauseIcon').style.display = 'none';
    }
}

export async function spotifyTogglePlay() {
    const data = await spotifyApi('/me/player');

    console.log('[Spotify] Player state:', data);

    let device = data?.device;

    // Se não tem device no player, busca na lista de devices
    if (!device) {
        console.log('[Spotify] Buscando devices disponíveis...');
        const devicesData = await spotifyApi('/me/player/devices');
        console.log('[Spotify] Devices response:', devicesData);
        
        if (devicesData === null) {
            console.error('[Spotify] Falha ao buscar devices - spotifyApi retornou null (verifique logs acima para erro 403/401)');
        } else if (devicesData && devicesData.devices && devicesData.devices.length > 0) {
            // Procura device ativo ou o primeiro disponível
            device = devicesData.devices.find(d => d.is_active) || devicesData.devices[0];
            console.log('[Spotify] Device encontrado na lista:', device.name, device.is_active, device.id);
        } else {
            console.warn('[Spotify] Lista de devices vazia:', devicesData);
        }
    }

    if (!device) {
        console.warn('[Spotify] Nenhum device ativo detectado');
        alert('Nenhum dispositivo Spotify ativo detectado. Verifique se o Spotify está aberto e tocando no desktop/celular.');
        return;
    }

    console.log('[Spotify] Device ativo:', device.name, device.type, device.supports_volume, device.id);

    // Tenta transferir playback para este device se não for o ativo
    if (!device.is_active) {
        console.log('[Spotify] Transferindo playback para:', device.name);
        await spotifyApi('/me/player', 'PUT', { device_ids: [device.id] });
    }

    if (data.is_playing) {
        await spotifyApi('/me/player/pause', 'PUT');
    } else {
        await spotifyApi('/me/player/play', 'PUT');
    }

    setTimeout(updateSpotifyNowPlaying, 300);
}

export async function spotifyNext() {
    await spotifyApi('/me/player/next', 'POST');
    setTimeout(updateSpotifyNowPlaying, 300);
}

export async function spotifyPrevious() {
    await spotifyApi('/me/player/previous', 'POST');
    setTimeout(updateSpotifyNowPlaying, 300);
}

export async function spotifySetVolume(value) {
    await spotifyApi(`/me/player/volume?volume_percent=${value}`, 'PUT');
}

function checkSpotifyCallback() {
    const urlParams = new URLSearchParams(window.location.search);
    const code = urlParams.get('code');

    if (code) {
        console.log('[Spotify] Callback detectado com code');
        window.history.replaceState({}, document.title, window.location.pathname);
        exchangeCodeForToken(code);
    }
}

async function initSpotify() {
    try {
        await initSpotifyConfig();
        console.log('[Spotify] Config inicializado');
    } catch (error) {
        console.error('[Spotify] Falha ao inicializar config:', error);
        return;
    }

    checkSpotifyCallback();

    const savedToken = localStorage.getItem('spotify_access_token');
    const tokenExpiry = localStorage.getItem('spotify_token_expiry');

    if (savedToken && tokenExpiry && Date.now() < parseInt(tokenExpiry)) {
        spotifyAccessToken = savedToken;
        console.log('[Spotify] Token válido encontrado no storage');
        showSpotifyPlayer();
        await updateSpotifyNowPlaying();
        startSpotifyRefresh();
        setInterval(updateSpotifyNowPlaying, 5000);
    } else if (localStorage.getItem('spotify_refresh_token')) {
        console.log('[Spotify] Tentando renovar token expirado...');
        await refreshSpotifyToken();
        if (spotifyAccessToken) {
            showSpotifyPlayer();
            await updateSpotifyNowPlaying();
            startSpotifyRefresh();
            setInterval(updateSpotifyNowPlaying, 5000);
        }
    }
}

document.addEventListener('DOMContentLoaded', initSpotify);