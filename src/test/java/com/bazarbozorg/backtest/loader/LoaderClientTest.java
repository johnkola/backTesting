package com.bazarbozorg.backtest.loader;

import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;


import static org.junit.jupiter.api.Assertions.*;

class LoaderClientTest {

    @Test
    @DisplayName("query values are URL-encoded")
    void encodesQueryValues() {
        assertEquals("nn-feedforward", LoaderClient.encode("nn-feedforward"));
        assertEquals("a+b%2Fc", LoaderClient.encode("a b/c"));
    }
}
