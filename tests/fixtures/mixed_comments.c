/***********************************************************
 * Module: Mixed Comments
 * This is a decorative section header.
 ***********************************************************/

/**
 * @brief Properly documented function.
 * @version 1.0
 */
void Documented_After_Decorative(void) {
    setup();
}

/* Regular C comment, not doxygen */
void After_Regular_Comment(void) {
    work();
}

// Single-line comment
void After_Line_Comment(void) {
    more_work();
}

/***********************************************************
 * Another section header
 ***********************************************************/
void After_Second_Decorative(void) {
    stuff();
}
