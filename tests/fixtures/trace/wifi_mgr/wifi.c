/**
 * @brief Connect to WiFi after delay.
 * @version 1.0
 * @req REQ-0252
 * @handles EVENT:PAIRING_STARTED
 * @emits EVENT:EVENT_WIFI_MGR_WIFI_IP_ACQUIRED
 */
void WIFIMGR_STACONNECTAFTERDELAY(void) {
    connect_wifi();
}
