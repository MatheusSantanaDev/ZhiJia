import { getConfigValue } from '../utils/config.js';
import { getLocationDataFromCep, getCoordsFromCity } from '../utils/location.js';

export async function fetchWeatherData() {
    console.log("Iniciando busca de dados...");
    try {
        const zipCode = getConfigValue('weather_zip_code', '38414-553');
        const cep = zipCode.replace(/\D/g, '');

        const locationInfo = await getLocationDataFromCep(cep);
        if (!locationInfo) throw new Error("CEP inválido ou não encontrado na BrasilAPI.");

        let coords = { lat: locationInfo.lat, lon: locationInfo.lon };

        if (!coords.lat || !coords.lon) {
            console.warn("Coordenadas não encontradas para o CEP. Usando nome da cidade.");
            coords = await getCoordsFromCity(locationInfo.locationName);
            if (!coords) throw new Error(`Não foi possível encontrar coordenadas para a cidade: ${locationInfo.locationName}`);
        }
        console.log("Coordenadas finais utilizadas:", coords);

        const openMeteoURL = `https://api.open-meteo.com/v1/forecast?latitude=${coords.lat}&longitude=${coords.lon}&current=temperature_2m,relative_humidity_2m,apparent_temperature,weather_code&daily=weather_code,temperature_2m_max&hourly=relative_humidity_2m&timezone=auto&forecast_days=6`;
        const response = await fetch(openMeteoURL);
        if (!response.ok) throw new Error(`Erro na API Open-Meteo: ${response.statusText}`);

        const weatherData = await response.json();
        return { weatherData, locationName: locationInfo.locationName };

    } catch (error) {
        console.error("ERRO FINAL:", error.message);
        throw error;
    }
}