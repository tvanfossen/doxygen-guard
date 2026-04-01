/**
 * @brief Connect to WiFi after delay.
 * @version 1.0
 * @req REQ-0252
 * @handles EVENT_PAIRING_STARTED
 * @emits EVENT_WIFI_IP_ACQUIRED
 */
void WiFi_ConnectAfterDelay(void) {
    connect_wifi();
}
