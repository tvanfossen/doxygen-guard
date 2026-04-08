/**
 * @brief Function with invalid tag values.
 * @version 1.0
 * @req INVALID-FORMAT
 * @sends BADPREFIX_EVENT
 * @calls modfunc
 */
void Bad_Tags(void) {
    do_stuff();
}

/**
 * @brief Function with valid tag values.
 * @version 1.0
 * @req REQ-0001
 * @sends EVENT_DATA_READY
 * @calls mod::func
 */
void Good_Tags(void) {
    do_other_stuff();
}
