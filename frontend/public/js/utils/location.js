export async function getLocationDataFromCep(cep) {
    const brasilApiURL = `https://brasilapi.com.br/api/cep/v2/${cep}`;
    try {
        const response = await fetch(brasilApiURL);
        if (!response.ok) return null;
        const data = await response.json();

        if (data.location && data.location.coordinates && data.location.coordinates.latitude) {
            return {
                lat: data.location.coordinates.latitude,
                lon: data.location.coordinates.longitude,
                locationName: `${data.city}, ${data.state}`
            };
        } else if (data.city) {
            return {
                lat: null,
                lon: null,
                locationName: `${data.city}, ${data.state}`
            };
        }
        return null;
    } catch (error) {
        console.error("[getLocationDataFromCep] Falhou:", error);
        return null;
    }
}

export async function getCoordsFromCity(cityNameWithState) {
    const cityNameOnly = cityNameWithState.split(',')[0].trim();

    const geocodingURL = `https://geocoding-api.open-meteo.com/v1/search?name=${encodeURIComponent(cityNameOnly)}&count=1&language=pt&format=json`;
    try {
        const response = await fetch(geocodingURL);
        if (!response.ok) return null;
        const data = await response.json();

        if (data.results && data.results[0]) {
            return {
                lat: data.results[0].latitude,
                lon: data.results[0].longitude
            };
        }
        return null;
    } catch (error) {
        console.error("[getCoordsFromCity] Falhou:", error);
        return null;
    }
}