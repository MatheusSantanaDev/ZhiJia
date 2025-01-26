#include <WiFi.h>
#include <SPIFFS.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include <HTTPClient.h>
#include <mqtt_client.h>

// Pinos do LED RGB
const int ledPinRed = 25;
const int ledPinGreen = 26;
const int ledPinBlue = 27;

// Credenciais Wi-Fi e Duck DNS
char ssid[32];
char password[64];
String adminUser;
String adminPassword;
String duckDNSToken;
String duckDNSDomain;

// MQTT
const char* mqttServer = "mqtt://broker.hivemq.com";
const char* mqttTopic = "home/led/color";
esp_mqtt_client_handle_t mqttClient;

// Servidor HTTP
WebServer server(80);

// Configurações do LED
void setupLED() {
    ledcSetup(0, 5000, 8);
    ledcSetup(1, 5000, 8);
    ledcSetup(2, 5000, 8);
    ledcAttachPin(ledPinRed, 0);
    ledcAttachPin(ledPinGreen, 1);
    ledcAttachPin(ledPinBlue, 2);
}

// Função para ajustar a cor do LED
void setColor(int red, int green, int blue) {
    ledcWrite(0, red);
    ledcWrite(1, green);
    ledcWrite(2, blue);
}

// Carregar configuração do SPIFFS
void loadConfig() {
    if (!SPIFFS.begin(true)) {
        Serial.println("Falha ao montar o SPIFFS!");
        return;
    }

    File file = SPIFFS.open("/config.json", "r");
    if (!file) {
        Serial.println("Erro ao abrir config.json");
        return;
    }

    JsonDocument doc;
    DeserializationError error = deserializeJson(doc, file);
    if (error) {
        Serial.println("Erro ao carregar JSON");
        return;
    }

    strlcpy(ssid, doc["wifi_ssid"], sizeof(ssid));
    strlcpy(password, doc["wifi_password"], sizeof(password));
    adminUser = doc["admin_user"].as<String>();
    adminPassword = doc["admin_password"].as<String>();
    duckDNSToken = doc["duckdns_token"].as<String>();
    duckDNSDomain = doc["duckdns_domain"].as<String>();
    file.close();
}

// Função para atualizar o IP no Duck DNS
void updateDuckDNS() {
    HTTPClient http;
    
    // Obter o IP público via serviço externo (usando HTTP)
    http.begin("http://api.ipify.org");  // Usando HTTP para obter o IP
    int httpCode = http.GET();
    
    String ip;
    if (httpCode == 200) {
        ip = http.getString();  // IP público
        Serial.println("IP público obtido: " + ip);
    } else {
        Serial.println("Erro ao obter IP público");
        http.end();
        return;
    }

    // Atualiza o Duck DNS com o IP público (usando HTTP)
    String url = "http://www.duckdns.org/update?domains=" + duckDNSDomain + "&token=" + duckDNSToken + "&ip=" + ip;

    // Envia a requisição para o Duck DNS (usando HTTP)
    http.begin(url);
    httpCode = http.GET();
    
    if (httpCode == 200) {
        String response = http.getString();
        Serial.println("Atualização do Duck DNS: " + response);
    } else {
        Serial.printf("Erro ao atualizar DNS: %d\n", httpCode);
    }
    http.end();
}

// Autenticação no servidor HTTP
bool authenticate() {
    if (!server.authenticate(adminUser.c_str(), adminPassword.c_str())) {
        server.requestAuthentication();
        return false;
    }
    return true;
}
// Rota para servir o HTML
void handleRoot() {
    if (!authenticate()) return;
    File file = SPIFFS.open("/index.html", "r");
    if (!file) {
        server.send(404, "text/plain", "Página não encontrada");
        return;
    }
    server.streamFile(file, "text/html");
    file.close();
}

// Callback MQTT
int mqtt_event_handler(esp_mqtt_event_handle_t event) {
    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            Serial.println("Conectado ao broker MQTT!");
            esp_mqtt_client_subscribe(event->client, mqttTopic, 0);
            break;

        case MQTT_EVENT_DISCONNECTED:
            Serial.println("Desconectado do broker MQTT!");
            break;

        case MQTT_EVENT_DATA: {
            char colorData[32];
            strncpy(colorData, event->data, event->data_len);
            colorData[event->data_len] = '\0';
            Serial.printf("Comando recebido via MQTT: %s\n", colorData);

            int red, green, blue;
            sscanf(colorData, "%d,%d,%d", &red, &green, &blue);
            setColor(red, green, blue);
            break;
        }

        default:
            Serial.printf("Evento MQTT desconhecido: %d\n", event->event_id);
            break;
    }

    return ESP_OK;
}

void setupMQTT() {
    esp_mqtt_client_config_t mqttConfig = {};
    mqttConfig.uri = mqttServer;
    mqttConfig.event_handle = mqtt_event_handler;

    mqttClient = esp_mqtt_client_init(&mqttConfig);
    esp_mqtt_client_start(mqttClient);
}

void setup() {
    Serial.begin(115200);

    loadConfig();

    // Conexão Wi-Fi
    WiFi.begin(ssid, password);
    while (WiFi.status() != WL_CONNECTED) {
        delay(1000);
        Serial.println("Conectando ao Wi-Fi...");
    }
    Serial.print("Wi-Fi conectado!");

    updateDuckDNS();
    setupLED();
    setupMQTT();

    // Configurar servidor HTTP
    server.on("/", HTTP_GET, handleRoot);
    server.begin();
    Serial.println("Servidor HTTP iniciado!");
}

void loop() {
    server.handleClient();
}
