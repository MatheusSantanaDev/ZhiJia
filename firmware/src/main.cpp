#include <WiFi.h>
#include <SPIFFS.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <mqtt_client.h>
#include <ESP32Servo.h>
#include "TuyaLocal.h"

// Pinos do LED RGB
const int ledPinRed = 25;
const int ledPinGreen = 26;
const int ledPinBlue = 27;

// Pino Servo Motor
const int servoPin = 15;
Servo myServo;

// Pinos dos sensores de fim de curso (usar pull-up interno, sensor fecha para GND)
const int sensorOpenPin = 32;   // Fim de curso "aberto"
const int sensorClosedPin = 33; // Fim de curso "fechado"
int currentDirection = 0;       // -1 = fechando, 0 = parado, 1 = abrindo

// Variáveis de configuração
char ssid[32] = "";
char password[64] = "";
String adminUser = "";
String adminPassword = "";
String duckDNSToken = "";
String duckDNSDomain = "";
String mqttServer = "";
int mqttPort = 1883;
int serverPort = 80;

// Configuração da fita Tuya
String stripIP = "";
String stripDeviceId = "";
String stripLocalKey = "";
TuyaLocal* strip = nullptr;

WebServer server(80);
esp_mqtt_client_handle_t mqttClient;
const char* mqttTopic       = "home/led/color";
const char* servoTopic      = "home/servo/angle";
const char* stripColorTopic = "home/strip/color";
const char* stripPowerTopic = "home/strip/power";
const char* ledSetTopic     = "home/led/set";
const char* ledStateTopic   = "home/led/state";
const char* ledStatusTopic  = "home/led/status";
const char* ledDiscoveryTopic = "homeassistant/light/zhijia_led_rgb/config";
const char* ledDiscoveryJson =
    "{\"name\":\"LED RGB\",\"unique_id\":\"zhijia_esp32_led_rgb\",\"schema\":\"json\","
    "\"command_topic\":\"home/led/set\",\"state_topic\":\"home/led/state\","
    "\"availability_topic\":\"home/led/status\",\"payload_available\":\"online\","
    "\"payload_not_available\":\"offline\",\"brightness\":true,"
    "\"supported_color_modes\":[\"rgb\"],"
    "\"device\":{\"identifiers\":[\"zhijia_esp32\"],\"name\":\"ZhiJia ESP32\","
    "\"manufacturer\":\"ZhiJia\",\"model\":\"esp32dev\"}}";

// Consumo de energia (simulado pelo ESP32) ========================
// O ESP32 simula circuitos separados (fogão, tomadas, luzes), cada um com
// seu tópico home/energy/<circuito>/... . O total da casa é montado no
// Home Assistant somando estes circuitos com os dispositivos reais
// (lavadora, TV, geladeira) que reportam pela própria integração.
const char* energyPowerTopic          = "home/energy/power";        // total do ESP32 (W)
const char* energyTotalTopic          = "home/energy/energy_total"; // total do ESP32 (kWh)
const char* energyPowerDiscoveryTopic = "homeassistant/sensor/zhijia_esp32_power/config";
const char* energyTotalDiscoveryTopic = "homeassistant/sensor/zhijia_esp32_energy_total/config";

// Compartilha o LWT da luz: é o mesmo aparelho, um status só
const char* energyPowerDiscoveryJson =
    "{\"name\":\"Consumo Instantâneo\",\"default_entity_id\":\"sensor.zhijia_esp32_power\","
    "\"unique_id\":\"zhijia_esp32_power\",\"state_topic\":\"home/energy/power\","
    "\"unit_of_measurement\":\"W\",\"device_class\":\"power\",\"state_class\":\"measurement\","
    "\"availability_topic\":\"home/led/status\",\"payload_available\":\"online\","
    "\"payload_not_available\":\"offline\","
    "\"device\":{\"identifiers\":[\"zhijia_esp32\"],\"name\":\"ZhiJia ESP32\","
    "\"manufacturer\":\"ZhiJia\",\"model\":\"esp32dev\"}}";

const char* energyTotalDiscoveryJson =
    "{\"name\":\"Energia Consumida\",\"default_entity_id\":\"sensor.zhijia_esp32_energy_total\","
    "\"unique_id\":\"zhijia_esp32_energy_total\",\"state_topic\":\"home/energy/energy_total\","
    "\"unit_of_measurement\":\"kWh\",\"device_class\":\"energy\",\"state_class\":\"total_increasing\","
    "\"availability_topic\":\"home/led/status\",\"payload_available\":\"online\","
    "\"payload_not_available\":\"offline\","
    "\"device\":{\"identifiers\":[\"zhijia_esp32\"],\"name\":\"ZhiJia ESP32\","
    "\"manufacturer\":\"ZhiJia\",\"model\":\"esp32dev\"}}";

// Trecho final repetido no discovery de cada circuito: mesmo LWT da luz
// (é o mesmo aparelho) e mesmo bloco de device.
const char* energyDiscoverySufixo =
    ",\"availability_topic\":\"home/led/status\",\"payload_available\":\"online\","
    "\"payload_not_available\":\"offline\","
    "\"device\":{\"identifiers\":[\"zhijia_esp32\"],\"name\":\"ZhiJia ESP32\","
    "\"manufacturer\":\"ZhiJia\",\"model\":\"esp32dev\"}}";

bool mqttConectado = false;


// Conexão WiFi ========================================================

const char* wifiStatusStr(wl_status_t status) {
    switch (status) {
        case WL_IDLE_STATUS:      return "IDLE";
        case WL_NO_SSID_AVAIL:    return "NO_SSID_AVAIL (rede nao encontrada)";
        case WL_SCAN_COMPLETED:   return "SCAN_COMPLETED";
        case WL_CONNECTED:        return "CONNECTED";
        case WL_CONNECT_FAILED:   return "CONNECT_FAILED (autenticacao/senha)";
        case WL_CONNECTION_LOST:  return "CONNECTION_LOST";
        case WL_DISCONNECTED:     return "DISCONNECTED";
        default:                  return "DESCONHECIDO";
    }
}

// Códigos de motivo (esp_wifi_disconnect_reason_t) mais comuns
void printWifiReason(uint8_t reason) {
    const char* msg = "outro";
    switch (reason) {
        case 2:  msg = "auth expirada"; break;
        case 3:  msg = "AP encerrou a autenticacao"; break;
        case 5:  msg = "AP com muitos clientes (limite)"; break;
        case 15: msg = "timeout do handshake 4-way -> SENHA ERRADA"; break;
        case 200: msg = "beacon timeout -> rede fora de alcance (ou 5 GHz)"; break;
        case 201: msg = "AP nao encontrado -> SSID so 2.4 GHz / invisivel / fora de alcance"; break;
        case 202: msg = "falha de autenticacao -> SENHA ERRADA"; break;
        case 203: msg = "falha de associacao"; break;
        case 204: msg = "timeout de handshake -> SENHA ERRADA"; break;
    }
    Serial.printf("[WiFi] Desconectado (reason=%u: %s)\n", reason, msg);
}

void wifiEvent(WiFiEvent_t event, arduino_event_info_t info) {
    switch (event) {
        case ARDUINO_EVENT_WIFI_STA_GOT_IP:
            Serial.println("[WiFi] Conectado! IP: " + WiFi.localIP().toString() +
                           " | Gateway: " + WiFi.gatewayIP().toString());
            break;
        case ARDUINO_EVENT_WIFI_STA_DISCONNECTED:
            printWifiReason(info.wifi_sta_disconnected.reason);
            break;
        case ARDUINO_EVENT_WIFI_STA_START:
            Serial.println("[WiFi] Estacao iniciada");
            break;
        default:
            break;
    }
}

// Localiza o AP de 2.4 GHz com o SSID desejado (o ESP32 não vê 5 GHz,
// e em roteadores com banda dupla/steering o ESP perde tempo no AP errado)
bool findAP24(uint8_t* bssid, int& canal) {
    Serial.println("[WiFi] Escaneando redes (2.4 GHz)...");
    int n = WiFi.scanNetworks();
    bool found = false;
    for (int i = 0; i < n; i++) {
        if (strcmp(WiFi.SSID(i).c_str(), ssid) == 0 && WiFi.channel(i) <= 14) {
            memcpy(bssid, WiFi.BSSID(i), 6);
            canal = WiFi.channel(i);
            found = true;
            Serial.printf("[WiFi] AP encontrado: canal %d, BSSID %02X:%02X:%02X:%02X:%02X:%02X (sinal %d dBm)\n",
                          canal, bssid[0], bssid[1], bssid[2], bssid[3], bssid[4], bssid[5], WiFi.RSSI(i));
            break;
        }
    }
    WiFi.scanDelete();
    if (!found) Serial.println("[WiFi] SSID nao visto na varredura de 2.4 GHz.");
    return found;
}

bool connectWiFi(int tentativas = 3) {
    if (strlen(ssid) == 0) {
        Serial.println("[WiFi] SSID VAZIO - /config.json nao foi carregado do SPIFFS.");
        Serial.println("[WiFi] Rode: pio run -t uploadfs  (envia data/config.json para o ESP)");
        return false;
    }

    static bool eventoRegistrado = false;
    if (!eventoRegistrado) {
        WiFi.onEvent(wifiEvent);
        eventoRegistrado = true;
    }
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);

    uint8_t bssid[6] = {0};
    int canal = 0;
    bool apEncontrado = findAP24(bssid, canal);

    for (int t = 1; t <= tentativas; t++) {
        if (apEncontrado) {
            WiFi.begin(ssid, password, canal, bssid); // fixa no AP de 2.4 GHz
        } else {
            WiFi.begin(ssid, password);
        }
        Serial.printf("[WiFi] Tentativa %d/%d - conectando a \"%s\"", t, tentativas, ssid);

        unsigned long inicio = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - inicio < 15000) {
            delay(500);
            Serial.print(".");
        }
        Serial.println();

        if (WiFi.status() == WL_CONNECTED) {
            WiFi.setAutoReconnect(true);
            return true;
        }
        Serial.printf("[WiFi] Sem sucesso. status=%s\n", wifiStatusStr(WiFi.status()));
        WiFi.disconnect(false, false);
        delay(1000);
    }

    Serial.println("[WiFi] Nao conectou depois das tentativas. Verifique:");
    Serial.println("[WiFi]   1. Rede de 2.4 GHz (ESP32 NAO suporta 5 GHz)");
    Serial.println("[WiFi]   2. SSID e senha iguais aos do roteador (sem espacos extras)");
    Serial.println("[WiFi]   3. Se o motivo acima for 201/200 = rede nao vista pelo ESP");
    return false;
}

// Duck DNS ============================================================

String getPublicIP() {
    HTTPClient http;
    http.begin("http://api.ipify.org");
    
    if (http.GET() == HTTP_CODE_OK) {
        return http.getString();
    }
    return "";
}

void updateDuckDNS() {
    if (duckDNSDomain.isEmpty() || duckDNSToken.isEmpty()) {
        Serial.println("[DuckDNS] Nao configurado - pulando.");
        return;
    }
    HTTPClient http;
    String publicIP = getPublicIP();
    
    if (publicIP.isEmpty()) {
        Serial.println("Falha ao obter IP público");
        return;
    }

    String url = "http://www.duckdns.org/update?" +
                 String("domains=") + duckDNSDomain +
                 String("&token=") + duckDNSToken +
                 String("&ip=") + publicIP;

    http.begin(url);
    int httpCode = http.GET();
    
    if (httpCode == HTTP_CODE_OK) {
        Serial.println("Duck DNS atualizado: " + http.getString());
    } else {
        Serial.println("Erro ao atualizar Duck DNS");
    }
    http.end();
}

// Controle LED ========================================================

void setupLED() {
    ledcSetup(0, 5000, 8);
    ledcSetup(1, 5000, 8);
    ledcSetup(2, 5000, 8);
    
    ledcAttachPin(ledPinRed, 0);
    ledcAttachPin(ledPinGreen, 1);
    ledcAttachPin(ledPinBlue, 2);
}

void setColor(uint8_t r, uint8_t g, uint8_t b) {
    ledcWrite(0, r);
    ledcWrite(1, g);
    ledcWrite(2, b);
}

bool ledOn = false;
uint8_t ledR = 255;
uint8_t ledG = 255;
uint8_t ledB = 255;
uint8_t ledBrightness = 255;

void applyLed() {
    if (!ledOn) {
        setColor(0, 0, 0);
        return;
    }
    setColor((ledR * ledBrightness) / 255,
             (ledG * ledBrightness) / 255,
             (ledB * ledBrightness) / 255);
}

void publishLedState() {
    char state[160];
    snprintf(state, sizeof(state),
             "{\"state\":\"%s\",\"color_mode\":\"rgb\",\"color\":{\"r\":%d,\"g\":%d,\"b\":%d},\"brightness\":%d}",
             ledOn ? "ON" : "OFF", ledR, ledG, ledB, ledBrightness);
    esp_mqtt_client_publish(mqttClient, ledStateTopic, state, 0, 0, 1);
}

// Estado da luz persistido no SPIFFS: a cor escolhida sobrevive a
// reboots e quedas de energia. Assim, quando o ESP32 volta (ou a
// luz é ligada), ela reacende na mesma cor de antes, e não no
// branco padrao.
const char* ledStatePath = "/led_state.json";
bool ledStateDirty = false;
unsigned long ultimoSaveLed = 0;

void saveLedStateNow() {
    File f = SPIFFS.open(ledStatePath, FILE_WRITE);
    if (!f) {
        Serial.println("[LED] Falha ao abrir led_state.json para escrever");
        return;
    }
    char buf[128];
    snprintf(buf, sizeof(buf),
             "{\"on\":%s,\"r\":%d,\"g\":%d,\"b\":%d,\"brightness\":%d}",
             ledOn ? "true" : "false", ledR, ledG, ledB, ledBrightness);
    f.print(buf);
    f.close();
}

// Marca que o estado mudou. A gravacao acontece em persistLedState()
// com debounce: arrastar o controle de cor manda muitos comandos por
// segundo e o flash do ESP32 tem ciclos de escrita limitados.
void markLedStateChanged() {
    ledStateDirty = true;
}

void persistLedState() {
    if (!ledStateDirty) return;
    if (millis() - ultimoSaveLed < 2000) return;
    ledStateDirty = false;
    ultimoSaveLed = millis();
    saveLedStateNow();
}

void loadLedState() {
    File f = SPIFFS.open(ledStatePath, FILE_READ);
    if (!f) return;

    JsonDocument doc;
    DeserializationError err = deserializeJson(doc, f);
    f.close();
    if (err) {
        Serial.printf("[LED] led_state.json invalido (%s) - usando padrao\n",
                      err.c_str());
        return;
    }

    ledOn = doc["on"] | false;
    ledR = constrain((int)(doc["r"] | 255), 0, 255);
    ledG = constrain((int)(doc["g"] | 255), 0, 255);
    ledB = constrain((int)(doc["b"] | 255), 0, 255);
    ledBrightness = constrain((int)(doc["brightness"] | 255), 1, 255);
    applyLed();
    Serial.printf("[LED] Estado restaurado: %s cor=(%d,%d,%d) brilho=%d\n",
                  ledOn ? "ON" : "OFF", ledR, ledG, ledB, ledBrightness);
}

// Controle Servo ======================================================
void setupServo() {
    myServo.attach(servoPin, 500, 2400);
    myServo.write(90); // Inicia parado (90 = neutro para servo de rotação contínua)

    // Configura sensores de fim de curso com pull-up interno
    pinMode(sensorOpenPin, INPUT_PULLUP);
    pinMode(sensorClosedPin, INPUT_PULLUP);
}

void stopServo() {
    myServo.write(90);
    currentDirection = 0;
    Serial.println("[SERVO] Motor parado.");
}

void setServoSpeed(int speed) {
    // speed: -100 a 100
    // Negativo = fechando (anti-horário)
    // Positivo = abrindo (horário)
    speed = constrain(speed, -100, 100);

    // Verifica fim de curso antes de mover
    bool isFullyOpen = digitalRead(sensorOpenPin) == LOW;
    bool isFullyClosed = digitalRead(sensorClosedPin) == LOW;

    // Bloqueia movimento se já está no fim de curso correspondente
    if (speed > 0 && isFullyOpen) {
        Serial.println("[SERVO] Já está totalmente aberto. Movimento bloqueado.");
        stopServo();
        return;
    }
    if (speed < 0 && isFullyClosed) {
        Serial.println("[SERVO] Já está totalmente fechado. Movimento bloqueado.");
        stopServo();
        return;
    }

    // Define direção atual
    if (speed > 0) currentDirection = 1;
    else if (speed < 0) currentDirection = -1;
    else currentDirection = 0;

    // Mapeia para o servo: 0 = anti-horário max, 90 = parado, 180 = horário max
    int servoValue = map(speed, -100, 100, 0, 180);
    myServo.write(servoValue);

    if (speed != 0) {
        Serial.printf("[SERVO] Motor girando. Velocidade: %d, Direção: %s\n",
            abs(speed), speed > 0 ? "ABRINDO" : "FECHANDO");
    }
}

void checkEndstops() {
    if (currentDirection == 0) return; // Motor parado, não precisa verificar

    bool isFullyOpen = digitalRead(sensorOpenPin) == LOW;
    bool isFullyClosed = digitalRead(sensorClosedPin) == LOW;

    if (currentDirection == 1 && isFullyOpen) {
        Serial.println("[SERVO] Fim de curso ABERTO acionado!");
        stopServo();
    }
    if (currentDirection == -1 && isFullyClosed) {
        Serial.println("[SERVO] Fim de curso FECHADO acionado!");
        stopServo();
    }
}

// Energia (simulação de consumo por circuito) =====================
// Cada circuito tem sua própria curva de 24 h (0h -> 23h) em Watts e seu
// próprio acumulador de kWh. Serve como "casa de teste" enquanto o medidor
// real não existe.
// Obs.: TV Samsung e geladeira LG ficam de fora aqui: quando ligarem nas
// integrações deles entram direto no total que o HA monta.
struct Circuito {
    const char* id;        // slug usado no tópico e no entity_id
    const char* nome;      // nome amigável exibido no HA
    float perfil[24];      // potência típica hora a hora (W)
    int   ruido;           // ±W do ruído do dia a dia
    int   chancePico;      // chance de pico, por 1000, a cada ciclo
    int   picoMin;         // W mínimos do pico
    int   picoMax;         // W máximos do pico
    float potenciaW;       // último valor calculado
    float kwh;             // energia acumulada deste circuito
};

Circuito circuitos[] = {
    // Fogão: pico no café (6-8h), almoço (11-13h) e jantar (18-21h)
    {"fogao", "Fogão",
     {   0,   0,   0,   0,   0,   0, 150, 600, 200,   0,   0, 800,
      1600, 900,   0,   0,   0, 200, 900,1500,1200, 400,   0,   0},
     40, 8, 300, 900, 0, 0},
    // Tomadas: standby do roteador o dia todo + TV/notebook à noite
    {"tomadas", "Tomadas",
     {  60,  55,  50,  50,  50,  55,  70,  90, 100, 110, 110, 100,
      120, 130, 120, 120, 130, 150, 180, 200, 210, 200, 150,  90},
     12, 4, 60, 250, 0, 0},
    // Luz da sala: só à noite (17h-23h)
    {"luz_sala", "Luz Sala",
     {   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,   0,
        0,   0,   0,   0,   0,  10,  40,  50,  50,  50,  40,  20},
     4, 0, 0, 0, 0, 0},
    // Luz do quarto: madrugada e antes de dormir
    {"luz_quarto", "Luz Quarto",
     {  15,  10,   0,   0,   0,   0,   5,   0,   0,   0,   0,   0,
        0,   0,   0,   0,   0,   0,   0,   0,   5,  15,  15,  15},
     3, 0, 0, 0, 0, 0},
    // Luz da cozinha: manhã (6-8h) e noite (18-22h)
    {"luz_cozinha", "Luz Cozinha",
     {   0,   0,   0,   0,   0,   0,  20,  30,  15,   0,   0,   0,
        0,   0,   0,   0,   0,   0,  30,  40,  40,  30,  15,   0},
     3, 0, 0, 0, 0, 0},
};
const int NUM_CIRCUITOS = sizeof(circuitos) / sizeof(circuitos[0]);

unsigned long ultimaPublicacaoEnergia = 0;
const unsigned long intervaloEnergiaMs = 10000; // publica a cada 10 s

// Hora local via NTP; se ainda não sincronizou, usa um "dia sintético"
// contado a partir do boot para a curva não ficar congelada.
bool horarioAtual(struct tm& tinfo) {
    if (getLocalTime(&tinfo, 50)) return true;

    unsigned long segDoDia = (millis() / 1000UL) % 86400UL;
    memset(&tinfo, 0, sizeof(tinfo));
    tinfo.tm_hour = segDoDia / 3600;
    tinfo.tm_min  = (segDoDia % 3600) / 60;
    tinfo.tm_sec  = segDoDia % 60;
    return false;
}

// Potência instantânea de um circuito: interpola entre as horas para a curva
// subir/descer suavemente, soma o ruído do dia a dia e ocasionalmente um pico.
float potenciaCircuito(Circuito& c, const struct tm& tinfo) {
    int hora     = tinfo.tm_hour;
    int horaProx = (hora + 1) % 24;
    float fracao = (tinfo.tm_min + tinfo.tm_sec / 60.0f) / 60.0f;
    float base   = c.perfil[hora] + (c.perfil[horaProx] - c.perfil[hora]) * fracao;

    // O LED RGB do próprio ESP32 puxa da tomada da sala
    if (strcmp(c.id, "tomadas") == 0 && ledOn) base += 8.0f;

    base += (float)random(-c.ruido, c.ruido + 1);

    // Picos esporádicos: forno ligado, carregador, nobreak
    if (c.chancePico > 0 && random(0, 1000) < c.chancePico)
        base += (float)random(c.picoMin, c.picoMax + 1);

    return base < 0.0f ? 0.0f : base;
}

// Integra a potência de cada circuito no tempo e publica no MQTT
// (a cada intervaloEnergiaMs)
void publicarEnergia() {
    if (!mqttConectado || mqttClient == nullptr) return;

    unsigned long agora = millis();
    if (ultimaPublicacaoEnergia == 0) {
        ultimaPublicacaoEnergia = agora; // primeira chamada só marca o início
        return;
    }
    if (agora - ultimaPublicacaoEnergia < intervaloEnergiaMs) return;

    float dtSeg = (agora - ultimaPublicacaoEnergia) / 1000.0f;
    ultimaPublicacaoEnergia = agora;

    struct tm tinfo;
    horarioAtual(tinfo);

    char payload[24];
    char topico[96];
    float somaW   = 0.0f;
    float somaKwh = 0.0f;

    for (int i = 0; i < NUM_CIRCUITOS; i++) {
        Circuito& c = circuitos[i];
        c.potenciaW = potenciaCircuito(c, tinfo);
        c.kwh += c.potenciaW * dtSeg / 3600000.0f; // W·s -> kWh
        somaW   += c.potenciaW;
        somaKwh += c.kwh;

        snprintf(topico, sizeof(topico), "home/energy/%s/power", c.id);
        snprintf(payload, sizeof(payload), "%.1f", c.potenciaW);
        esp_mqtt_client_publish(mqttClient, topico, payload, 0, 0, 1);

        snprintf(topico, sizeof(topico), "home/energy/%s/energy_total", c.id);
        snprintf(payload, sizeof(payload), "%.4f", c.kwh);
        esp_mqtt_client_publish(mqttClient, topico, payload, 0, 0, 1);
    }

    // Total do ESP32 = soma dos circuitos simulados por ele
    snprintf(payload, sizeof(payload), "%.1f", somaW);
    esp_mqtt_client_publish(mqttClient, energyPowerTopic, payload, 0, 0, 1);

    snprintf(payload, sizeof(payload), "%.4f", somaKwh);
    esp_mqtt_client_publish(mqttClient, energyTotalTopic, payload, 0, 0, 1);
}

void publicarDiscoveryEnergia(esp_mqtt_client_handle_t client) {
    esp_mqtt_client_publish(client, energyPowerDiscoveryTopic, energyPowerDiscoveryJson, 0, 0, 1);
    esp_mqtt_client_publish(client, energyTotalDiscoveryTopic, energyTotalDiscoveryJson, 0, 0, 1);

    // Um par de sensores (potência + energia) por circuito simulado
    char topico[96];
    char json[640];
    for (int i = 0; i < NUM_CIRCUITOS; i++) {
        const Circuito& c = circuitos[i];

        snprintf(topico, sizeof(topico),
                 "homeassistant/sensor/zhijia_esp32_%s_power/config", c.id);
        snprintf(json, sizeof(json),
            "{\"name\":\"%s Potência\",\"default_entity_id\":\"sensor.zhijia_esp32_%s_power\","
            "\"unique_id\":\"zhijia_esp32_%s_power\",\"state_topic\":\"home/energy/%s/power\","
            "\"unit_of_measurement\":\"W\",\"device_class\":\"power\",\"state_class\":\"measurement\"%s",
            c.nome, c.id, c.id, c.id, energyDiscoverySufixo);
        esp_mqtt_client_publish(client, topico, json, 0, 0, 1);

        snprintf(topico, sizeof(topico),
                 "homeassistant/sensor/zhijia_esp32_%s_energy_total/config", c.id);
        snprintf(json, sizeof(json),
            "{\"name\":\"%s Energia\",\"default_entity_id\":\"sensor.zhijia_esp32_%s_energy_total\","
            "\"unique_id\":\"zhijia_esp32_%s_energy_total\",\"state_topic\":\"home/energy/%s/energy_total\","
            "\"unit_of_measurement\":\"kWh\",\"device_class\":\"energy\",\"state_class\":\"total_increasing\"%s",
            c.nome, c.id, c.id, c.id, energyDiscoverySufixo);
        esp_mqtt_client_publish(client, topico, json, 0, 0, 1);
    }

    Serial.printf("[MQTT] Discovery da energia publicado: %d circuitos + total\n",
                  NUM_CIRCUITOS);
}

// MQTT ================================================================

esp_err_t mqtt_event_handler(esp_mqtt_event_handle_t event) {
    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            Serial.println("[MQTT] Conectado ao broker!");
            mqttConectado = true;
            esp_mqtt_client_subscribe(event->client, mqttTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", mqttTopic);
            esp_mqtt_client_subscribe(event->client, servoTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", servoTopic);
            esp_mqtt_client_subscribe(event->client, stripColorTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", stripColorTopic);
            esp_mqtt_client_subscribe(event->client, stripPowerTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", stripPowerTopic);
            esp_mqtt_client_subscribe(event->client, ledSetTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", ledSetTopic);
            esp_mqtt_client_publish(event->client, ledDiscoveryTopic, ledDiscoveryJson, 0, 0, 1);
            esp_mqtt_client_publish(event->client, ledStatusTopic, "online", 0, 0, 1);
            publishLedState();
            publicarDiscoveryEnergia(event->client);
            ultimaPublicacaoEnergia = 0; // não integra o tempo desconectado
            Serial.println("[MQTT] Discovery e estado da luz publicados");
            break;

        case MQTT_EVENT_DISCONNECTED:
            Serial.println("[MQTT] Desconectado!");
            mqttConectado = false;
            break;

        case MQTT_EVENT_DATA: {
            char payload[192];
            char topic[128];
            strncpy(payload, event->data, std::min((size_t)event->data_len, sizeof(payload) - 1));
            payload[std::min((size_t)event->data_len, sizeof(payload) - 1)] = '\0';
            strncpy(topic, event->topic, std::min((size_t)event->topic_len, sizeof(topic) - 1));
            topic[std::min((size_t)event->topic_len, sizeof(topic) - 1)] = '\0';

            // Verifica em qual tópico a mensagem chegou
            if (strcmp(topic, mqttTopic) == 0) {
                // Lógica do RGB LED
                int r, g, b;
                if (sscanf(payload, "%d,%d,%d", &r, &g, &b) == 3) {
                    ledR = constrain(r, 0, 255);
                    ledG = constrain(g, 0, 255);
                    ledB = constrain(b, 0, 255);
                    ledOn = true;
                    applyLed();
                    publishLedState();
                    markLedStateChanged();
                    Serial.printf("[RGB] Nova cor: R=%d, G=%d, B=%d\n", r, g, b);
                }
            } else if (strcmp(topic, ledSetTopic) == 0) {
                JsonDocument doc;
                if (deserializeJson(doc, payload)) {
                    Serial.println("[RGB] Payload invalido recebido do HA");
                    return ESP_OK;
                }

                const char* haState = doc["state"];
                if (haState != nullptr) {
                    ledOn = strcmp(haState, "ON") == 0;
                }

                int brightness = doc["brightness"] | 0;
                if (brightness > 0) {
                    ledBrightness = constrain(brightness, 1, 255);
                }

                int newR = doc["color"]["r"] | (int)ledR;
                int newG = doc["color"]["g"] | (int)ledG;
                int newB = doc["color"]["b"] | (int)ledB;
                ledR = constrain(newR, 0, 255);
                ledG = constrain(newG, 0, 255);
                ledB = constrain(newB, 0, 255);

                applyLed();
                publishLedState();
                markLedStateChanged();
                Serial.printf("[HA] state=%s cor=(%d,%d,%d) brilho=%d\n",
                              ledOn ? "ON" : "OFF", ledR, ledG, ledB, ledBrightness);
            } else if (strcmp(topic, servoTopic) == 0) {
                JsonDocument doc;
                DeserializationError error = deserializeJson(doc, payload);

                if (error) {
                    Serial.print(F("[SERVO] Falha ao parsear JSON: "));
                    Serial.println(error.c_str());
                    return ESP_FAIL;
                }

                int speed = doc["speed"]; // Positivo = abrir, Negativo = fechar
                setServoSpeed(speed);

            } else if (strcmp(topic, stripColorTopic) == 0) {
                if (strip == nullptr) break;
                int r, g, b;
                if (sscanf(payload, "%d,%d,%d", &r, &g, &b) == 3) {
                    strip->setColor(r, g, b);
                    Serial.printf("[STRIP] Cor: R=%d G=%d B=%d\n", r, g, b);
                }

            } else if (strcmp(topic, stripPowerTopic) == 0) {
                if (strip == nullptr) break;
                if (strcmp(payload, "on") == 0) {
                    strip->setPower(true);
                    Serial.println("[STRIP] Ligada");
                } else if (strcmp(payload, "off") == 0) {
                    strip->setPower(false);
                    Serial.println("[STRIP] Desligada");
                }
            }
            break;
        }
        case MQTT_EVENT_ERROR:
            Serial.println("[MQTT] Erro na conexão!");
            if (event->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
                Serial.printf("[MQTT] Código do erro: %d\n", event->error_handle->esp_transport_sock_errno);
            }
            break;

        default:
            break;
    }
    return ESP_OK;
}

void setupMQTT() {
    esp_mqtt_client_config_t mqttConfig = {};
    mqttConfig.uri = mqttServer.c_str();
    mqttConfig.event_handle = mqtt_event_handler;
    mqttConfig.lwt_topic = ledStatusTopic;
    mqttConfig.lwt_msg = "offline";
    mqttConfig.lwt_qos = 0;
    mqttConfig.lwt_retain = 1;

    mqttClient = esp_mqtt_client_init(&mqttConfig);
    esp_mqtt_client_start(mqttClient);
}

// Web Server ==========================================================
bool authenticate() {
    if (!server.authenticate(adminUser.c_str(), adminPassword.c_str())) {
        server.requestAuthentication();
        return false;
    }
    return true;
}

void serveFile(const String& path, const String& contentType) {
    File file = SPIFFS.open(path, "r");
    if (file) {
        server.streamFile(file, contentType);
        file.close();
    } else {
        server.send(404, "text/plain", "Arquivo não encontrado");
    }
}

String getMimeType(const String& filename) {
    if (filename.endsWith(".html")) return "text/html";
    if (filename.endsWith(".css")) return "text/css";
    if (filename.endsWith(".js")) return "text/javascript";
    if (filename.endsWith(".svg")) return "image/svg+xml";
    return "text/plain";
}

void setupWebServer() {
    // Handler para a raiz
    server.on("/", HTTP_GET, []() {
        if (!authenticate()) return;
        serveFile("/index.html", "text/html");
    });

    // Handler para arquivos estáticos (CSS, JS, etc)
    server.onNotFound([]() {
        if (!authenticate()) return;
        
        String path = server.uri();
        Serial.println("Tentando servir: " + path);
        
        if (SPIFFS.exists(path)) {
            serveFile(path, getMimeType(path));
        } else {
            server.send(404, "text/plain", "Arquivo não encontrado: " + path);
        }
    });

    server.begin();
}
// Funções de inicialização ============================================

void loadConfig() {
    File configFile = SPIFFS.open("/config.json");
    if (!configFile) {
        Serial.println("config.json não encontrado!");
        return;
    }

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, configFile);
    if (error) {
        Serial.println("Erro ao ler config.json! JSON invalido (comentarios // ou vírgula extra).");
        Serial.println(String("  -> ") + error.c_str());
        configFile.close();
        return;
    }

    // Carregar configurações (| "" evita crash se a chave nao existir)
    strlcpy(ssid, doc["wifi_ssid"] | "", sizeof(ssid));
    strlcpy(password, doc["wifi_password"] | "", sizeof(password));
    adminUser     = doc["admin_user"].as<String>();
    adminPassword = doc["admin_password"].as<String>();
    duckDNSToken  = doc["duckdns_token"].as<String>();
    duckDNSDomain = doc["duckdns_domain"].as<String>();
    mqttServer    = doc["mqtt_server"].as<String>();
    mqttPort      = doc["mqtt_port"];
    serverPort    = doc["server_port"];

    // Fita LED Tuya
    stripIP       = doc["strip_ip"].as<String>();
    stripDeviceId = doc["strip_device_id"].as<String>();
    stripLocalKey = doc["strip_local_key"].as<String>();

    configFile.close();

    Serial.printf("[CFG] /config.json carregado. SSID=\"%s\" MQTT=%s\n",
                  ssid, mqttServer.c_str());
    if (strlen(ssid) == 0) Serial.println("[CFG] Atencao: SSID vazio!");
}

void initSystems() {
    if (!SPIFFS.begin(true)) {
        Serial.println("Erro ao inicializar SPIFFS!");
        while(1) delay(1000);
    }
    loadConfig();
    setupLED();
    loadLedState(); // restaura a ultima cor (sobrevive a reboots/quedas)
    setupServo();

    if (!stripIP.isEmpty() && !stripDeviceId.isEmpty() && !stripLocalKey.isEmpty()) {
        strip = new TuyaLocal(stripIP, stripDeviceId, stripLocalKey);
        Serial.printf("[STRIP] Fita configurada em %s\n", stripIP.c_str());
    }
}

// Serviços que precisam de rede. Rodam assim que o WiFi estiver up, seja no
// boot ou depois de uma reconexão no loop() - sem isso, se o WiFi falhar no
// boot o MQTT nunca seria iniciado e o ESP32 ficaria mudo até reiniciar.
bool redePronta = false;

void iniciarServicosDeRede() {
    if (redePronta) return;
    updateDuckDNS();
    setupMQTT();
    // Hora local (UTC-3, sem horário de verão no Brasil desde 2019)
    configTime(-3 * 3600, 0, "pool.ntp.org", "time.nist.gov");
    redePronta = true;
}

void setup() {
    Serial.begin(115200);
    delay(1000);
    Serial.println("\n=== ZhiJia ESP32 - Boot ===");

    // Inicializar sistemas
    initSystems();
    bool wifiOk = connectWiFi();

    if (wifiOk) {
        iniciarServicosDeRede();
    } else {
        Serial.println("[Boot] Sem WiFi no boot. Web server inicia; DuckDNS/MQTT esperam a rede subir no loop().");
    }
    randomSeed(esp_random());
    setupWebServer();
}

void loop() {
    // Rede subiu (boot sem WiFi ou reconexão)? Então sim, inicia DuckDNS/MQTT.
    if (WiFi.status() == WL_CONNECTED) iniciarServicosDeRede();

    server.handleClient();
    checkEndstops();
    persistLedState(); // salva a cor quando mudou (debounce de 2 s)
    publicarEnergia(); // simula e publica o consumo a cada 10 s

    // Reconexão Wi-Fi: re-escaneia (o AP pode ter mudado de canal/BSSID)
    static unsigned long ultimaTentativa = 0;
    if (WiFi.status() != WL_CONNECTED && millis() - ultimaTentativa > 15000) {
        ultimaTentativa = millis();
        Serial.println("[WiFi] Conexao perdida - reconectando...");
        connectWiFi(2);
    }
}