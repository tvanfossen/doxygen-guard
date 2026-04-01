/**
 * @brief Start MQTT connection.
 * @version 1.0
 * @req REQ-0252
 * @handles EVENT_EVENT_MQTT_START_CONNECTION
 */
void startMqttConnection(void) {
    mqtt_init();
    mqtt_connect();
}
