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

// MQTT ================================================================

esp_err_t mqtt_event_handler(esp_mqtt_event_handle_t event) {
    switch (event->event_id) {
        case MQTT_EVENT_CONNECTED:
            Serial.println("[MQTT] Conectado ao broker!");
            esp_mqtt_client_subscribe(event->client, mqttTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", mqttTopic);
            esp_mqtt_client_subscribe(event->client, servoTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", servoTopic);
            esp_mqtt_client_subscribe(event->client, stripColorTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", stripColorTopic);
            esp_mqtt_client_subscribe(event->client, stripPowerTopic, 0);
            Serial.printf("[MQTT] Inscrito no tópico: %s\n", stripPowerTopic);
            break;

        case MQTT_EVENT_DISCONNECTED:
            Serial.println("[MQTT] Desconectado!");
            break;

        case MQTT_EVENT_DATA: {
            char payload[32];
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
                    setColor(r, g, b);
                    Serial.printf("[RGB] Nova cor: R=%d, G=%d, B=%d\n", r, g, b);
                }
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
}

void initSystems() {
    if (!SPIFFS.begin(true)) {
        Serial.println("Erro ao inicializar SPIFFS!");
        while(1) delay(1000);
    }
    loadConfig();
    setupLED();
    setupServo();

    if (!stripIP.isEmpty() && !stripDeviceId.isEmpty() && !stripLocalKey.isEmpty()) {
        strip = new TuyaLocal(stripIP, stripDeviceId, stripLocalKey);
        Serial.printf("[STRIP] Fita configurada em %s\n", stripIP.c_str());
    }
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
    checkEndstops();
}