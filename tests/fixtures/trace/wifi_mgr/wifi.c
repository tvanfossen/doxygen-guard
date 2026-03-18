/**
 * @brief Connect to WiFi after delay.
 * @version 1.0
 * @req REQ-0252
 * @handles EVENT:PAIRING_STARTED
 * @emits EVENT:WIFI_IP_ACQUIRED
 */
void WiFi_ConnectAfterDelay(void) {
    connect_wifi();
}
