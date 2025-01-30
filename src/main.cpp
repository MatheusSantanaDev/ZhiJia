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

WebServer server(80);
esp_mqtt_client_handle_t mqttClient;
const char* mqttTopic = "home/led/color";


// Conexão WiFi ========================================================

void connectWiFi() {
    WiFi.begin(ssid, password);
    Serial.print("Conectando ao WiFi");
    while (WiFi.status() != WL_CONNECTED) {
        delay(500);
        Serial.print(".");
    }
    Serial.println("\nConectado!" );
    Serial.println("Acesse em: http://" + duckDNSDomain + ":" + serverPort);
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

// MQTT ================================================================

int mqtt_event_handler(esp_mqtt_event_handle_t event) {
    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            Serial.println("[MQTT] Conectado ao broker!");
            if (esp_mqtt_client_subscribe(event->client, mqttTopic, 0) != -1) {
                Serial.printf("[MQTT] Inscrito no tópico: %s\n", mqttTopic);
            } else {
                Serial.println("[MQTT] Erro na inscrição!");
            }
            break;

        case MQTT_EVENT_DISCONNECTED:
            Serial.println("[MQTT] Desconectado!");
            break;

        case MQTT_EVENT_DATA: {
            char payload[32] = {0};
            size_t len = (event->data_len < sizeof(payload)-1) ? event->data_len : sizeof(payload)-1;
            memcpy(payload, event->data, len);
            
            int r, g, b;
            if (sscanf(payload, "%d,%d,%d", &r, &g, &b) == 3) {
                setColor(r, g, b);
                Serial.printf("[MQTT] Nova cor: R=%d, G=%d, B=%d\n", r, g, b);
            } else {
                Serial.println("[MQTT] Formato inválido! Use: R,G,B");
            }
            break;
        }

        case MQTT_EVENT_ERROR:
            Serial.println("[MQTT] Erro na conexão!");
            if (event->error_handle->error_type == MQTT_ERROR_TYPE_TCP_TRANSPORT) {
                Serial.printf("[MQTT] Código do erro: %d\n", event->error_handle->esp_transport_sock_errno);
            }
            break;
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
        Serial.println("Erro ao ler config.json!");
        return;
    }

    // Carregar configurações
    strlcpy(ssid, doc["wifi_ssid"], sizeof(ssid));
    strlcpy(password, doc["wifi_password"], sizeof(password));
    adminUser = doc["admin_user"].as<String>();
    adminPassword = doc["admin_password"].as<String>();
    duckDNSToken = doc["duckdns_token"].as<String>();
    duckDNSDomain = doc["duckdns_domain"].as<String>();
    mqttServer = doc["mqtt_server"].as<String>();
    mqttPort = doc["mqtt_port"];
    serverPort = doc["server_port"];

    configFile.close();
}

void initSystems() {
    if (!SPIFFS.begin(true)) {
        Serial.println("Erro ao inicializar SPIFFS!");
        while(1) delay(1000);
    }
    loadConfig();
    setupLED();
}

void setup() {
    Serial.begin(115200);
    
    // Inicializar sistemas
    initSystems();
    connectWiFi();
    updateDuckDNS();
    setupMQTT();
    setupWebServer();
}

void loop() {
    server.handleClient();
}