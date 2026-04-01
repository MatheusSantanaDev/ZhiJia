// Configuração do Spotify - SUBSTITUA pelo seu Client ID
const SPOTIFY_CLIENT_ID = 'SEU_CLIENT_ID_AQUI';
const SPOTIFY_REDIRECT_URI = window.location.origin + '/';
const SPOTIFY_SCOPES = [
    'user-read-playback-state',
    'user-modify-playback-state',
    'user-read-currently-playing'
].join(' ');

let spotifyAccessToken = null;
let spotifyRefreshInterval = null;

// Gera string aleatória para PKCE
function generateRandomString(length) {
    const possible = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    const values = crypto.getRandomValues(new Uint8Array(length));
    return values.reduce((acc, x) => acc + possible[x % possible.length], '');
}

// Gera code challenge para PKCE
async function generateCodeChallenge(codeVerifier) {
    const data = new TextEncoder().encode(codeVerifier);
    const digest = await crypto.subtle.digest('SHA-256', data);
    return btoa(String.fromCharCode(...new Uint8Array(digest)))
        .replace(/=/g, '')
        .replace(/\+/g, '-')
        .replace(/\//g, '_');
}

// Inicia o login com Spotify
async function spotifyLogin() {
    const codeVerifier = generateRandomString(64);
    localStorage.setItem('spotify_code_verifier', codeVerifier);

    const codeChallenge = await generateCodeChallenge(codeVerifier);

    const params = new URLSearchParams({
        client_id: SPOTIFY_CLIENT_ID,
        response_type: 'code',
        redirect_uri: SPOTIFY_REDIRECT_URI,
        code_challenge_method: 'S256',
        code_challenge: codeChallenge,
        scope: SPOTIFY_SCOPES
    });

    window.location.href = `https://accounts.spotify.com/authorize?${params.toString()}`;
}

// Troca o código de autorização por um token
async function exchangeCodeForToken(code) {
    const codeVerifier = localStorage.getItem('spotify_code_verifier');

    const response = await fetch('https://accounts.spotify.com/api/token', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: new URLSearchParams({
            client_id: SPOTIFY_CLIENT_ID,
            grant_type: 'authorization_code',
            code: code,
            redirect_uri: SPOTIFY_REDIRECT_URI,
            code_verifier: codeVerifier
        })
    });

    const data = await response.json();

    if (data.access_token) {
        spotifyAccessToken = data.access_token;
        localStorage.setItem('spotify_access_token', data.access_token);
        localStorage.setItem('spotify_refresh_token', data.refresh_token);
        localStorage.setItem('spotify_token_expiry', Date.now() + (data.expires_in * 1000));

        showSpotifyPlayer();
        updateSpotifyNowPlaying();
        startSpotifyRefresh();
    }
}

// Atualiza o token usando refresh token
async function refreshSpotifyToken() {
    const refreshToken = localStorage.getItem('spotify_refresh_token');
    if (!refreshToken) return;

    const response = await fetch('https://accounts.spotify.com/api/token', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/x-www-form-urlencoded'
        },
        body: new URLSearchParams({
            client_id: SPOTIFY_CLIENT_ID,
            grant_type: 'refresh_token',
            refresh_token: refreshToken
        })
    });

    const data = await response.json();

    if (data.access_token) {
        spotifyAccessToken = data.access_token;
        localStorage.setItem('spotify_access_token', data.access_token);
        localStorage.setItem('spotify_token_expiry', Date.now() + (data.expires_in * 1000));

        if (data.refresh_token) {
            localStorage.setItem('spotify_refresh_token', data.refresh_token);
        }
    }
}

// Inicia o refresh automático do token
function startSpotifyRefresh() {
    if (spotifyRefreshInterval) clearInterval(spotifyRefreshInterval);
    // Refresh a cada 50 minutos (token expira em 60)
    spotifyRefreshInterval = setInterval(refreshSpotifyToken, 50 * 60 * 1000);
}

// Mostra o player e esconde o botão de conectar
function showSpotifyPlayer() {
    document.getElementById('spotifyNotConnected').style.display = 'none';
    document.getElementById('spotifyPlayer').style.display = 'block';
}

// Faz requisição para a API do Spotify
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

    if (response.status === 204) return null;
    if (!response.ok) return null;

    return response.json();
}

// Atualiza informações da música atual
async function updateSpotifyNowPlaying() {
    const data = await spotifyApi('/me/player/currently-playing');

    if (data && data.item) {
        document.getElementById('spotifyTrackName').textContent = data.item.name;
        document.getElementById('spotifyArtistName').textContent = data.item.artists.map(a => a.name).join(', ');

        if (data.item.album.images.length > 0) {
            document.getElementById('spotifyAlbumArt').src = data.item.album.images[0].url;
        }

        // Atualiza ícone play/pause
        const isPlaying = data.is_playing;
        document.getElementById('spotifyPlayIcon').style.display = isPlaying ? 'none' : 'block';
        document.getElementById('spotifyPauseIcon').style.display = isPlaying ? 'block' : 'none';
    }
}

// Toggle play/pause
async function spotifyTogglePlay() {
    const data = await spotifyApi('/me/player');

    if (data && data.is_playing) {
        await spotifyApi('/me/player/pause', 'PUT');
    } else {
        await spotifyApi('/me/player/play', 'PUT');
    }

    setTimeout(updateSpotifyNowPlaying, 300);
}

// Próxima música
async function spotifyNext() {
    await spotifyApi('/me/player/next', 'POST');
    setTimeout(updateSpotifyNowPlaying, 300);
}

// Música anterior
async function spotifyPrevious() {
    await spotifyApi('/me/player/previous', 'POST');
    setTimeout(updateSpotifyNowPlaying, 300);
}

// Ajusta o volume
async function spotifySetVolume(value) {
    await spotifyApi(`/me/player/volume?volume_percent=${value}`, 'PUT');
}

// Verifica se há código de autorização na URL (callback)
function checkSpotifyCallback() {
    const urlParams = new URLSearchParams(window.location.search);
    const code = urlParams.get('code');

    if (code) {
        // Remove o código da URL
        window.history.replaceState({}, document.title, window.location.pathname);
        exchangeCodeForToken(code);
    }
}

// Inicializa o Spotify ao carregar a página
function initSpotify() {
    // Verifica callback primeiro
    checkSpotifyCallback();

    // Verifica se já tem token salvo
    const savedToken = localStorage.getItem('spotify_access_token');
    const tokenExpiry = localStorage.getItem('spotify_token_expiry');

    if (savedToken && tokenExpiry && Date.now() < parseInt(tokenExpiry)) {
        spotifyAccessToken = savedToken;
        showSpotifyPlayer();
        updateSpotifyNowPlaying();
        startSpotifyRefresh();

        // Atualiza a cada 5 segundos
        setInterval(updateSpotifyNowPlaying, 5000);
    } else if (localStorage.getItem('spotify_refresh_token')) {
        // Token expirado, tenta renovar
        refreshSpotifyToken().then(() => {
            if (spotifyAccessToken) {
                showSpotifyPlayer();
                updateSpotifyNowPlaying();
                startSpotifyRefresh();
                setInterval(updateSpotifyNowPlaying, 5000);
            }
        });
    }
}

// Executa quando o DOM estiver pronto
document.addEventListener('DOMContentLoaded', initSpotify);
