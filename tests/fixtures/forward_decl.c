void Module_Init(void);
int Module_Process(const char *data, size_t len);

/**
 * @brief The actual implementation.
 * @version 1.0
 */
void Module_Init(void) {
    setup();
}
