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
String mqttServer;
int mqttPort;
const char* mqttTopic = "home/led/color";
esp_mqtt_client_handle_t mqttClient;

// Servidor HTTP
WebServer server(80);
int serverPort;

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
    mqttServer = doc["mqtt_server"].as<String>();
    mqttPort = doc["mqtt_port"];
    serverPort = doc["server_port"];

    file.close();
}

// Função para atualizar o IP no Duck DNS
void updateDuckDNS() {
    HTTPClient http;
    
    http.begin("http://api.ipify.org"); // Obter o IP público via serviço externo
    int httpCode = http.GET();
    
    String ip;
    if (httpCode == 200) {
        ip = http.getString();  // IP público
        Serial.println(" IP público obtido.");
    } else {
        Serial.println(" Erro ao obter IP público");
        http.end();
        return;
    }

    // Atualiza o Duck DNS com o IP público
    String url = "http://www.duckdns.org/update?domains=" + duckDNSDomain + "&token=" + duckDNSToken + "&ip=" + ip;

    // Envia a requisição para o Duck DNS
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
            Serial.println("[MQTT] Conectado ao broker MQTT!");
            if (esp_mqtt_client_subscribe(event->client, mqttTopic, 0) != -1) {
                Serial.printf("[MQTT] Inscrito no tópico: %s\n", mqttTopic);
            } else {
                Serial.println("[MQTT] Falha ao se inscrever no tópico.");
            }
            break;

        case MQTT_EVENT_DISCONNECTED:
            Serial.println("[MQTT] Desconectado do broker MQTT!");
            break;

        case MQTT_EVENT_DATA: {
            Serial.printf("[MQTT] Dados recebidos do tópico: %.*s\n", event->topic_len, event->topic);

            // Garantindo que os dados sejam tratados como uma string válida
            char colorData[32] = {0}; // Inicializa com zeros
            size_t copyLen = (event->data_len < sizeof(colorData) - 1) ? event->data_len : sizeof(colorData) - 1;
            strncpy(colorData, event->data, copyLen);

            // Parsing dos valores RGB
            int red = 0, green = 0, blue = 0;
            if (sscanf(colorData, "%d,%d,%d", &red, &green, &blue) == 3) {
                Serial.printf("[MQTT] Definindo cor RGB para: R=%d, G=%d, B=%d\n", red, green, blue);
                setColor(red, green, blue); // Função definida pelo usuário
            } else {
                Serial.println("[MQTT] Formato de comando inválido. Use: R,G,B");
            }
            break;
        }
    }
    return ESP_OK;
}

void setupMQTT() {
    esp_mqtt_client_config_t mqttConfig = {};
    mqttConfig.uri = mqttServer.c_str();
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

    // Configurar servidor HTTP
    server.on("/", HTTP_GET, handleRoot);
    server.begin();
    Serial.println("Servidor HTTP iniciado!");
    Serial.println("Acesse em: http://" + duckDNSDomain + ":" + serverPort);

    setupMQTT();
    setupLED();
}

void loop() {
    server.handleClient();
}
